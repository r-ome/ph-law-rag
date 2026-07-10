import json
from pathlib import Path
from typing import Any

from app.evals import artifacts

_METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
_RAW_PRECISION = "llm_context_precision_with_reference"


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
