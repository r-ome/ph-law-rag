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
    """turn: {question, rewritten_question, answer, retrieved_chunks_json, sources_json}"""
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
                rewritten_question, answer, retrieved_chunks_json, sources_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                turn_id, session_id, next_index, turn["question"],
                turn.get("rewritten_question"), turn.get("answer"),
                turn.get("retrieved_chunks_json"), turn.get("sources_json"), _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return turn_id


def _truncate_title(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def list_conversations() -> list[dict]:
    """Sessions newest-first, with turn_count and a title (lazy: first question if unset)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                c.session_id,
                c.created_at,
                c.title,
                (SELECT COUNT(*) FROM conversation_turns t WHERE t.session_id = c.session_id)
                    AS turn_count,
                (SELECT t.question FROM conversation_turns t
                 WHERE t.session_id = c.session_id
                 ORDER BY t.turn_index ASC LIMIT 1) AS first_question
            FROM conversations c
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        title = d["title"] or (
            _truncate_title(d["first_question"]) if d["first_question"] else "New conversation"
        )
        out.append(
            {
                "session_id": d["session_id"],
                "created_at": d["created_at"],
                "title": title,
                "turn_count": d["turn_count"],
            }
        )
    return out


def get_conversation(session_id: str) -> dict | None:
    """Full thread oldest-first, each turn carrying its persisted citation sources."""
    import json

    conn = get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone() is None:
            return None
        rows = conn.execute(
            """
            SELECT turn_index, question, answer, sources_json
            FROM conversation_turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    turns: list[dict] = []
    for r in rows:
        raw = r["sources_json"]
        try:
            sources = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            sources = []
        turns.append(
            {
                "turn_index": r["turn_index"],
                "question": r["question"],
                "answer": r["answer"] or "",
                "sources": sources,
            }
        )
    return {"session_id": session_id, "turn_count": len(turns), "turns": turns}
