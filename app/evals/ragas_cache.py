import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ragas

from app.config import settings
from app.evals import artifacts

SCORER_VERSION = "ragas-cache-v1"
METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "llm_context_precision_with_reference",
    "context_recall",
]


def sample_from_result(row: dict) -> dict:
    return {
        "user_input": row["question"],
        "response": row["answer"],
        "retrieved_contexts": row["contexts"],
        "reference": row["ground_truth"],
    }


def cache_path() -> Path:
    return Path(settings.ragas_score_cache_path)


def cache_key(
    sample: dict,
    *,
    metric_names: list[str] | None = None,
    judge_model: str | None = None,
    embedding_model: str | None = None,
    scorer_version: str = SCORER_VERSION,
) -> str:
    # Deliberately content-addressed: row identity is excluded so edited questions
    # cannot reuse stale scores and existing cache entries remain useful.
    payload = {
        "sample": sample,
        "metric_names": metric_names or METRIC_NAMES,
        "judge_model": judge_model or settings.ragas_llm_model,
        "embedding_model": embedding_model or settings.ragas_embedding_model,
        "ragas_version": getattr(ragas, "__version__", "unknown"),
        "scorer_version": scorer_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or cache_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ragas_score_cache (
            cache_key TEXT PRIMARY KEY,
            scores_json TEXT NOT NULL,
            sample_json TEXT NOT NULL,
            metric_names_json TEXT NOT NULL,
            judge_model TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            ragas_version TEXT NOT NULL,
            scorer_version TEXT NOT NULL,
            source_tag TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_many(keys: list[str], path: Path | None = None) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    conn = _connect(path)
    try:
        found: dict[str, dict[str, Any]] = {}
        for key in keys:
            row = conn.execute(
                "SELECT scores_json FROM ragas_score_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row:
                found[key] = json.loads(row[0])
        return found
    finally:
        conn.close()


def put_many(
    rows: list[tuple[str, dict, dict[str, Any]]],
    *,
    source_tag: str | None = None,
    metric_names: list[str] | None = None,
    judge_model: str | None = None,
    path: Path | None = None,
) -> int:
    if not rows:
        return 0

    conn = _connect(path)
    now = datetime.now(timezone.utc).isoformat()
    metric_names = metric_names or METRIC_NAMES
    judge_model = judge_model or settings.ragas_llm_model
    try:
        for key, sample, scores in rows:
            conn.execute(
                """
                INSERT INTO ragas_score_cache (
                    cache_key, scores_json, sample_json, metric_names_json,
                    judge_model, embedding_model, ragas_version, scorer_version,
                    source_tag, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    scores_json = excluded.scores_json,
                    sample_json = excluded.sample_json,
                    metric_names_json = excluded.metric_names_json,
                    judge_model = excluded.judge_model,
                    embedding_model = excluded.embedding_model,
                    ragas_version = excluded.ragas_version,
                    scorer_version = excluded.scorer_version,
                    source_tag = excluded.source_tag,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(scores, ensure_ascii=False),
                    json.dumps(sample, ensure_ascii=False, sort_keys=True),
                    json.dumps(metric_names),
                    judge_model,
                    settings.ragas_embedding_model,
                    getattr(ragas, "__version__", "unknown"),
                    SCORER_VERSION,
                    source_tag,
                    now,
                    now,
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def stats(path: Path | None = None) -> dict[str, Any]:
    conn = _connect(path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM ragas_score_cache").fetchone()[0]
        by_model = conn.execute(
            """
            SELECT judge_model, COUNT(*)
            FROM ragas_score_cache
            GROUP BY judge_model
            ORDER BY judge_model
            """
        ).fetchall()
        by_source = conn.execute(
            """
            SELECT COALESCE(source_tag, 'unknown'), COUNT(*)
            FROM ragas_score_cache
            GROUP BY COALESCE(source_tag, 'unknown')
            ORDER BY 2 DESC, 1
            """
        ).fetchall()
        return {
            "path": str(path or cache_path()),
            "total": total,
            "by_judge_model": dict(by_model),
            "by_source_tag": dict(by_source),
        }
    finally:
        conn.close()


def clear(path: Path | None = None) -> int:
    conn = _connect(path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM ragas_score_cache").fetchone()[0]
        conn.execute("DELETE FROM ragas_score_cache")
        conn.commit()
        return int(total)
    finally:
        conn.close()


def seed_from_artifacts(run_tag: str, path: Path | None = None) -> dict[str, int]:
    run_path = artifacts.existing_path(run_tag, "run", required=True)
    scored_path = artifacts.existing_path(run_tag, "scored", required=True)

    run_rows = [
        json.loads(line)
        for line in run_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scored_rows = json.loads(scored_path.read_text(encoding="utf-8"))
    scored_by_question = {row["user_input"]: row for row in scored_rows}

    cache_rows = []
    skipped = 0
    for row in run_rows:
        scored = scored_by_question.get(row["question"])
        if not scored:
            skipped += 1
            continue
        if not all(name in scored for name in METRIC_NAMES):
            skipped += 1
            continue
        scores = {name: scored.get(name) for name in METRIC_NAMES}
        sample = sample_from_result(row)
        key = cache_key(sample)
        cache_rows.append((key, sample, scores))

    written = put_many(cache_rows, source_tag=run_tag, path=path)
    return {"written": written, "skipped": skipped}
