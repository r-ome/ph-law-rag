import json
from pathlib import Path

from app.config import settings


def _trace_dir() -> Path:
    return Path(settings.log_dir) / "traces"


def _iter_files_newest_first(date: str | None) -> list[Path]:
    d = _trace_dir()
    if not d.exists():
        return []
    if date:
        p = d / f"{date}.jsonl"
        return [p] if p.exists() else []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stem, reverse=True)


def _summary(rec: dict) -> dict:
    strat = rec.get("retrieval_strategy") or {}
    return {
        "trace_id": rec.get("trace_id", ""),
        "timestamp": rec.get("timestamp"),
        "trace_label": rec.get("trace_label"),
        "question": rec.get("question", ""),
        "strategy": strat.get("strategy"),
        "stage_counts": rec.get("stage_counts") or {},
        "latency_ms": rec.get("latency_ms"),
        "abstained": bool(rec.get("abstained", False)),
        "error": bool(rec.get("error", False)),
    }


def list_traces(limit: int = 50, date: str | None = None) -> list[dict]:
    """Newest-first trace summaries. Skips lines that don't parse."""
    out: list[dict] = []
    for path in _iter_files_newest_first(date):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict) and rec.get("trace_id"):
                out.append(_summary(rec))
                if len(out) >= limit:
                    return out
    return out


def get_trace(trace_id: str) -> dict | None:
    """Full record by id, newest file first. None if not found or unparseable."""
    for path in _iter_files_newest_first(None):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict) and rec.get("trace_id") == trace_id:
                return rec
    return None
