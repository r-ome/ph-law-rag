from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import uuid4

import structlog


@dataclass
class TraceCollector:
	stages: list[dict[str, Any]] = field(default_factory=list)

	def stage(
		self,
		name: str,
		in_n: int | None = None,
		out_n: int | None = None,
		ms: float | None = None,
		**fields: Any,
	) -> None:
		record: dict[str, Any] = {"name": name}
		if in_n is not None:
			record["in_n"] = in_n
		if out_n is not None:
			record["out_n"] = out_n
		if ms is not None:
			record["ms"] = round(ms, 2)
		record.update(fields)
		self.stages.append(record)


_trace_collector_var: contextvars.ContextVar[TraceCollector | None] = contextvars.ContextVar(
	"raglab_trace_collector",
	default=None,
)


def new_trace_id() -> str:
	return str(uuid4())


def current_trace_collector() -> TraceCollector | None:
	return _trace_collector_var.get()


def record_stage(
	name: str,
	in_n: int | None = None,
	out_n: int | None = None,
	ms: float | None = None,
	**fields: Any,
) -> None:
	collector = current_trace_collector()
	if collector is not None:
		collector.stage(name, in_n=in_n, out_n=out_n, ms=ms, **fields)


@contextmanager
def trace_context(
	*,
	trace_id: str,
	session_id: str | None = None,
	collector: TraceCollector | None = None,
) -> Iterator[TraceCollector | None]:
	structlog.contextvars.clear_contextvars()
	bindings: dict[str, str] = {"trace_id": trace_id}
	if session_id:
		bindings["session_id"] = session_id
	structlog.contextvars.bind_contextvars(**bindings)
	token = _trace_collector_var.set(collector)
	try:
		yield collector
	finally:
		_trace_collector_var.reset(token)
		structlog.contextvars.clear_contextvars()


@contextmanager
def stage_timer(name: str, in_n: int | None = None, **fields: Any) -> Iterator[dict[str, Any]]:
	state: dict[str, Any] = {}
	start = time.perf_counter()
	try:
		yield state
	finally:
		out_n = state.get("out_n")
		record_fields = dict(fields)
		record_fields.update(state.get("fields", {}))
		record_stage(
			name,
			in_n=in_n,
			out_n=out_n,
			ms=(time.perf_counter() - start) * 1000,
			**record_fields,
		)

