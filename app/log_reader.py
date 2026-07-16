import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

_LEVEL_RANK = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
_CORE_KEYS = {"timestamp", "level", "event", "logger"}


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
            extra = {k: v for k, v in rec.items() if k not in _CORE_KEYS}
            entry = {
                "timestamp": rec.get("timestamp"),
                "level": rec.get("level"),
                "event": rec.get("event"),
                "logger": rec.get("logger"),
                "raw": None,
                "extra": extra or None,
            }
        except (ValueError, TypeError):
            entry = {"timestamp": None, "level": None, "event": None, "logger": None, "raw": raw, "extra": None}
        rank = _LEVEL_RANK.get((entry["level"] or "").lower(), 0)
        if rank >= min_rank:
            out.append(entry)
    return out


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_logs_window(
    since: str, until: str, level: str | None = None, limit: int = 2000
) -> tuple[list[dict], bool]:
    """JSON app-log entries with since <= timestamp <= until, chronological.

    Scans app.log plus rotated backups (app.log.N, oldest first). Unparsable lines
    and lines without a parsable timestamp are skipped (they can't be windowed).
    Returns (entries, truncated); truncated=True when `limit` was hit.
    """
    limit = max(1, min(limit, 5000))
    lo, hi = _parse_ts(since), _parse_ts(until)
    if lo is None or hi is None:
        return [], False
    min_rank = _LEVEL_RANK.get((level or "").lower(), 0)
    log_dir = Path(settings.log_dir)
    backups = sorted(
        (p for p in log_dir.glob("app.log.*") if p.suffix.lstrip(".").isdigit()),
        key=lambda p: int(p.suffix.lstrip(".")),
        reverse=True,  # app.log.5 (oldest) -> app.log.1 (newest backup)
    )
    out: list[dict] = []
    truncated = False
    for path in [*backups, log_dir / "app.log"]:
        if not path.exists():
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < lo:
                continue
        except OSError:
            continue
        with path.open(encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                ts = _parse_ts(rec.get("timestamp"))
                if ts is None or ts < lo:
                    continue
                if ts > hi:
                    return out, truncated
                rank = _LEVEL_RANK.get((rec.get("level") or "").lower(), 0)
                if rank < min_rank:
                    continue
                if len(out) >= limit:
                    truncated = True
                    return out, truncated
                out.append({
                    "timestamp": rec.get("timestamp"),
                    "level": rec.get("level"),
                    "event": rec.get("event"),
                    "logger": rec.get("logger"),
                    "raw": None,
                })
    return out, truncated
