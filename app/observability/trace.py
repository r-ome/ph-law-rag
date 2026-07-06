from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.observability.logger import get_logger

logger = get_logger(__name__)
_warned_messages: set[str] = set()


def _warn_once(message: str, **fields: Any) -> None:
	if message in _warned_messages:
		return
	_warned_messages.add(message)
	try:
		logger.warning(message, **fields)
	except Exception:
		pass


class TraceWriter:
	def __init__(self, log_dir: str | Path | None = None) -> None:
		self.log_dir = Path(log_dir or settings.log_dir)

	def write(self, record: dict[str, Any]) -> None:
		if not settings.trace_logging_enabled:
			return
		try:
			timestamp = record.get("timestamp") or datetime.now(timezone.utc).isoformat()
			date = str(timestamp)[:10]
			trace_dir = self.log_dir / "traces"
			trace_dir.mkdir(parents=True, exist_ok=True)
			path = trace_dir / f"{date}.jsonl"
			with path.open("a", encoding="utf-8") as f:
				f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
		except Exception as exc:
			_warn_once("trace_write_failed", error=str(exc), log_dir=str(self.log_dir))


def prune_traces(days: int) -> dict[str, int]:
	cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
	trace_dir = Path(settings.log_dir) / "traces"
	deleted = 0
	if not trace_dir.exists():
		return {"deleted": deleted}
	for path in trace_dir.glob("*.jsonl"):
		try:
			file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
		except ValueError:
			continue
		if file_date < cutoff:
			try:
				path.unlink()
				deleted += 1
			except OSError as exc:
				_warn_once("trace_prune_failed", path=str(path), error=str(exc))
	return {"deleted": deleted}
