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
	capture_candidate_stages: bool = False
	candidate_stages: list[dict[str, Any]] = field(default_factory=list)

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

	def candidates(
		self,
		stage: str,
		results: list[Any],
		*,
		query_variant: str = "original",
		query_text: str = "",
		query_ordinal: int = 0,
		pool_role: str | None = None,
		score_field: str | None = None,
		survived_ids: set[str] | None = None,
		selected_ids: set[str] | None = None,
	) -> None:
		"""Snapshot a candidate pool without retaining mutable result objects."""
		if not self.capture_candidate_stages:
			return
		candidates: list[dict[str, Any]] = []
		for rank, result in enumerate(results, start=1):
			metadata = dict(getattr(result, "metadata", {}) or {})
			scores = dict(metadata.get("_retrieval_scores", {}) or {})
			score = float(getattr(result, "score", 0.0))
			if score_field:
				scores[score_field] = score
			candidate = {
				"rank": rank,
				"chunk_id": str(getattr(result, "chunk_id", "")),
				"text": str(getattr(result, "text", "")),
				"score": score,
				"metadata": metadata,
				**scores,
			}
			if survived_ids is not None:
				candidate["survived"] = candidate["chunk_id"] in survived_ids
			if selected_ids is not None:
				candidate["selected"] = candidate["chunk_id"] in selected_ids
			candidates.append(candidate)
		snapshot = {
				"stage": stage,
				"query_variant": query_variant,
				"query_text": query_text,
				"query_ordinal": query_ordinal,
				"candidates": candidates,
			}
		if pool_role is not None:
			snapshot["pool_role"] = pool_role
		self.candidate_stages.append(snapshot)


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


def capture_candidates(
	stage: str,
	results: list[Any],
	*,
	query_variant: str = "original",
	query_text: str = "",
	query_ordinal: int = 0,
	pool_role: str | None = None,
	score_field: str | None = None,
	survived_ids: set[str] | None = None,
	selected_ids: set[str] | None = None,
) -> None:
	collector = current_trace_collector()
	if collector is not None:
		collector.candidates(
			stage,
			results,
			query_variant=query_variant,
			query_text=query_text,
			query_ordinal=query_ordinal,
			pool_role=pool_role,
			score_field=score_field,
			survived_ids=survived_ids,
			selected_ids=selected_ids,
		)


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
