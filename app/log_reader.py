import json
from collections import deque
from pathlib import Path

from app.config import settings

_LEVEL_RANK = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def read_logs(lines: int = 200, level: str | None = None) -> list[dict]:
    """Last `lines` app-log entries (oldest->newest), optionally filtered to >= level."""
    lines = max(1, min(lines, 1000))
    path = Path(settings.log_dir) / "app.log"
    if not path.exists():
        return []
    min_rank = _LEVEL_RANK.get((level or "").lower(), 0)
    with path.open(encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=lines)
    out: list[dict] = []
    for raw in tail:
        raw = raw.rstrip("\n")
        if not raw:
            continue
        try:
            rec = json.loads(raw)
            entry = {
                "timestamp": rec.get("timestamp"),
                "level": rec.get("level"),
                "event": rec.get("event"),
                "logger": rec.get("logger"),
                "raw": None,
            }
        except (ValueError, TypeError):
            entry = {"timestamp": None, "level": None, "event": None, "logger": None, "raw": raw}
        rank = _LEVEL_RANK.get((entry["level"] or "").lower(), 0)
        if rank >= min_rank:
            out.append(entry)
    return out
