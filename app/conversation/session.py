import uuid
from datetime import datetime, timezone

from app.db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(session_id: str | None = None, title: str | None = None) -> str:
    session_id = session_id or str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO conversations(session_id, created_at, title) VALUES (?,?,?)",
            (session_id, _now(), title),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id


def session_exists(session_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def get_history(session_id: str, limit: int) -> list[dict]:
    """Most recent `limit` turns, returned oldest-first for prompt readability."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT turn_index, question, rewritten_question, answer
            FROM conversation_turns
            WHERE session_id = ?
            ORDER BY turn_index DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in reversed(rows)]


def append_turn(session_id: str, turn: dict) -> str:
    """turn: {question, rewritten_question, answer, retrieved_chunks_json}"""
    conn = get_connection()
    try:
        next_index = conn.execute(
            "SELECT COALESCE(MAX(turn_index) + 1, 0) AS n "
            "FROM conversation_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()["n"]
        turn_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO conversation_turns(
                turn_id, session_id, turn_index, question,
                rewritten_question, answer, retrieved_chunks_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                turn_id, session_id, next_index, turn["question"],
                turn.get("rewritten_question"), turn.get("answer"),
                turn.get("retrieved_chunks_json"), _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return turn_id
