import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals import artifacts

_METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
_RAW_PRECISION = "llm_context_precision_with_reference"
_EVAL_NOISE_FLOOR = 0.05
_QUALITY_BANDS = [
    {"key": "strong", "label": "Strong", "min": 0.85, "range": ">= 0.85"},
    {"key": "fair", "label": "Fair", "min": 0.70, "range": "0.70 - 0.85"},
    {"key": "weak", "label": "Weak", "min": None, "range": "< 0.70"},
]
_SPLIT_COPY = [
    {
        "key": "regression",
        "name": "Regression",
        "plain": (
            "Frozen benchmark - hash-locked so nobody edits questions quietly. "
            "The standing scoreboard used to compare runs over time."
        ),
    },
    {
        "key": "dev",
        "name": "Dev",
        "plain": "Practice set we study and tune against, so scores here run a little flattering.",
    },
    {
        "key": "holdout",
        "name": "Holdout",
        "plain": (
            "Sealed final exam. Only the total is ever read; inspecting one row burns it. "
            "The one honest test of generalization."
        ),
    },
]


def _split_counts(path: str | Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    dataset_path = Path(path)
    if not dataset_path.exists():
        return counts
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("split"), str):
                counts[row["split"]] += 1
    return counts


def eval_policy() -> dict:
    counts = _split_counts(settings.eval_dataset_path)
    return {
        "noise_floor": _EVAL_NOISE_FLOOR,
        "quality_bands": _QUALITY_BANDS,
        "splits": [
            {
                **split,
                "count": counts.get(split["key"], 0),
            }
            for split in _SPLIT_COPY
        ],
    }


def _parse_manifest() -> list[dict]:
    """Tolerant read of manifest.jsonl - skips malformed lines (never raises)."""
    path = artifacts.results_dir() / "manifest.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("tag"):
            rows.append(rec)
    return rows


def list_runs() -> list[dict]:
    """Manifest rows, newest-first (by date then tag; tag carries a trailing timestamp)."""
    rows = _parse_manifest()
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("tag") or "")), reverse=True)
    return rows


def _manifest_tags() -> set[str]:
    return {r["tag"] for r in _parse_manifest()}


def _norm_metrics(d: dict | None) -> dict:
    """Normalize a summary metric dict to the 4 canonical keys (nullable)."""
    d = d or {}
    return {
        "faithfulness": d.get("faithfulness"),
        "answer_relevancy": d.get("answer_relevancy"),
        "context_precision": d.get("context_precision", d.get(_RAW_PRECISION)),
        "context_recall": d.get("context_recall"),
    }


def _is_holdout(tag: str) -> bool:
    """Central reporting-surface guard; raw scoring artifacts remain on disk."""
    return bool((artifacts.load_meta(tag) or {}).get("holdout"))


def _load_summary(tag: str, *, holdout: bool = False) -> dict | None:
    p = artifacts.existing_path(tag, "summary")
    if p is None:
        return None
    raw = artifacts.read_json(p)
    by_cat = {}
    if not holdout:
        for cat, m in (raw.get("by_category") or {}).items():
            by_cat[cat] = {"n": (m or {}).get("n"), **_norm_metrics(m)}
    return {
        "overall": _norm_metrics(raw.get("overall")),
        "abstention": raw.get("abstention") or {},
        "by_category": by_cat,
    }


def get_run(tag: str) -> dict | None:
    """Detail: manifest-gated. Synthesizes core fields from manifest_row when meta.json absent."""
    if tag not in _manifest_tags():
        return None
    meta = artifacts.load_meta(tag)
    mrow = artifacts.manifest_row(tag)
    holdout = _is_holdout(tag)
    if holdout:
        from app.evals.holdout_ledger import log_holdout_aggregate_read

        log_holdout_aggregate_read(
            access_type="single_run",
            tags=[tag],
            purpose=(meta or {}).get("label") or mrow.get("label") or None,
            source="eval_store.get_run",
        )
    return {
        "tag": tag,
        "model": (meta or {}).get("model") or mrow.get("model"),
        "label": (meta or {}).get("label") or mrow.get("label"),
        "date": (meta or {}).get("date") or mrow.get("date"),
        "git_sha": (meta or {}).get("git_sha"),
        "question_count": (meta or {}).get("question_count") or mrow.get("questions"),
        "scored_count": (meta or {}).get("scored_count")
        if (meta or {}).get("scored_count") is not None
        else mrow.get("scored"),
        "summary": _load_summary(tag, holdout=holdout),
        "meta": meta,
    }


def _read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _row_debug(r: dict) -> dict:
    """Debug/pipeline fields passthrough — every field optional (legacy runs lack them)."""
    expected = r.get("expected_sources") or []
    retrieved = r.get("retrieved_sources") or []
    retrieved_set = set(retrieved)

    def _dict_or_none(key: str) -> dict | None:
        v = r.get(key)
        return v if isinstance(v, dict) else None

    return {
        "split": r.get("split"),
        "topic": r.get("topic"),
        "facet": r.get("facet"),
        "profile": r.get("profile"),
        "generator_model": r.get("generator_model"),
        "elapsed_s": r.get("elapsed_s"),
        "expected_sources": expected,
        "retrieved_sources": retrieved,
        "cited_sources": r.get("cited_sources") or [],
        "expected_missing": [s for s in expected if s not in retrieved_set],
        "selected_chunk_ids": r.get("selected_chunk_ids") or [],
        "evidence": _dict_or_none("evidence"),
        "corrective_retrieval": _dict_or_none("corrective_retrieval"),
        "model_choice": _dict_or_none("model_choice"),
        "debug_stages": [s for s in (r.get("debug_stages") or []) if isinstance(s, dict)],
    }


def get_rows(tag: str) -> dict | None:
    """Join of run.jsonl (all attempted) + scored.json metrics. Manifest-gated."""
    if tag not in _manifest_tags():
        return None
    if _is_holdout(tag):
        return {"tag": tag, "row_count": 0, "scored_count": 0, "rows": [], "holdout_redacted": True}
    paths = artifacts.paths_for_tag(tag)
    run_rows = _read_jsonl(paths.run)
    scored_by_id: dict[str, dict] = {}
    scored_by_content: dict[tuple, dict] = {}
    if paths.scored.exists():
        for s in artifacts.read_json(paths.scored):
            metrics = {
                "faithfulness": s.get("faithfulness"),
                "answer_relevancy": s.get("answer_relevancy"),
                "context_precision": s.get(_RAW_PRECISION),
                "context_recall": s.get("context_recall"),
            }
            if s.get("eval_id"):
                scored_by_id[s["eval_id"]] = metrics
            scored_by_content[(s.get("user_input"), s.get("reference"))] = metrics
    rows = []
    scored_count = 0
    for r in run_rows:
        metrics = scored_by_id.get(r.get("eval_id")) or scored_by_content.get(
            (r.get("question"), r.get("ground_truth"))
        )
        if metrics:
            scored_count += 1
        rows.append({
            "eval_id": r.get("eval_id"),
            "question": r.get("question", ""),
            "answer": r.get("answer", ""),
            "category": r.get("category"),
            "abstained": bool(r.get("abstained", False)),
            "ground_truth": r.get("ground_truth"),
            "contexts": r.get("contexts") or [],
            "faithfulness": (metrics or {}).get("faithfulness"),
            "answer_relevancy": (metrics or {}).get("answer_relevancy"),
            "context_precision": (metrics or {}).get("context_precision"),
            "context_recall": (metrics or {}).get("context_recall"),
            **_row_debug(r),
        })
    return {"tag": tag, "row_count": len(rows), "scored_count": scored_count, "rows": rows, "holdout_redacted": False}


def _delta(a: float | None, b: float | None) -> float | None:
    return round(a - b, 4) if a is not None and b is not None else None


def diff_runs(candidate: str, baseline: str) -> dict | None:
    """Summary-to-summary diff (overall + abstention + by_category). Both manifest-gated."""
    tags = _manifest_tags()
    if candidate not in tags or baseline not in tags:
        return None
    redacted = _is_holdout(candidate) or _is_holdout(baseline)
    if redacted:
        from app.evals.holdout_ledger import log_holdout_aggregate_read

        cand_meta = artifacts.load_meta(candidate) or {}
        base_meta = artifacts.load_meta(baseline) or {}
        log_holdout_aggregate_read(
            access_type="compare",
            tags=[candidate, baseline],
            purpose=cand_meta.get("label") or base_meta.get("label") or None,
            source="eval_store.diff_runs",
        )
    cand = _load_summary(candidate, holdout=redacted) or {"overall": _norm_metrics(None), "abstention": {}, "by_category": {}}
    base = _load_summary(baseline, holdout=redacted) or {"overall": _norm_metrics(None), "abstention": {}, "by_category": {}}

    overall_delta = {k: _delta(cand["overall"].get(k), base["overall"].get(k)) for k in _METRIC_KEYS}
    abst = {
        "candidate": (cand["abstention"] or {}).get("accuracy"),
        "baseline": (base["abstention"] or {}).get("accuracy"),
    }
    abst["delta"] = _delta(abst["candidate"], abst["baseline"])

    by_cat: dict[str, dict] = {}
    for cat in set(cand["by_category"]) | set(base["by_category"]):
        c = cand["by_category"].get(cat)
        b = base["by_category"].get(cat)
        if c and b:
            status = "matched"
        elif c and not b:
            status = "missing_baseline"
        else:
            status = "missing_candidate"
        by_cat[cat] = {
            "status": status,
            "candidate": _norm_metrics(c) if c else None,
            "baseline": _norm_metrics(b) if b else None,
            "delta": {k: _delta((c or {}).get(k), (b or {}).get(k)) for k in _METRIC_KEYS}
            if (c and b) else None,
        }
    return {
        "candidate_tag": candidate,
        "baseline_tag": baseline,
        "overall": {"candidate": cand["overall"], "baseline": base["overall"], "delta": overall_delta},
        "abstention": abst,
        "by_category": by_cat,
    }


def get_run_logs(tag: str, level: str | None = None, limit: int = 2000) -> dict | None:
    """App-log slice for the run's [started_at, completed_at] window. Manifest-gated."""
    if tag not in _manifest_tags():
        return None
    empty = {"tag": tag, "window": None, "entries": [], "count": 0,
             "truncated": False, "holdout_redacted": False}
    if _is_holdout(tag):
        return {**empty, "holdout_redacted": True}
    meta = artifacts.load_meta(tag) or {}
    started = meta.get("started_at")
    completed = meta.get("completed_at")
    if not started:
        return empty  # legacy run without meta.json — no window to query
    from datetime import datetime, timezone

    until = completed or datetime.now(timezone.utc).isoformat()
    from app.log_reader import read_logs_window

    entries, truncated = read_logs_window(since=started, until=until, level=level, limit=limit)
    return {
        "tag": tag,
        "window": {"started_at": started, "completed_at": completed},
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
        "holdout_redacted": False,
    }
