from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.evals.dataset import load_eval_dataset
from app.evals.integrity import paths_for, validate_sealed_bundle
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.pipeline.runner import prepare_answer_state
from app.pipeline.policy import resolve_policy
from app.pipeline.state import AnswerState
from app.retriever.adaptive_context import (
    packaging_pool_full_hash,
    selector_semantic_record,
)

CP_A0_SENTINELS = ("eval_075", "eval_129", "eval_053", "eval_039", "eval_055")
PHASE3_FROZEN_TAG = "phase3-sibling-aware-minilm"


def _rehydrate_frozen(selected: list[dict[str, Any]]):
    from app.retriever.types import RetrievalResult

    return [
        RetrievalResult(
            chunk_id=str(item["chunk_id"]),
            text=str(item.get("text", "")),
            score=float(item.get("score", 0.0)),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in selected
    ]


def _phase3_hashes(frozen_tag: str) -> dict[str, str]:
    rows, _meta = validate_sealed_bundle(paths_for(frozen_tag))
    wanted = set(CP_A0_SENTINELS)
    hashes = {
        row["eval_id"]: packaging_pool_full_hash(
            _rehydrate_frozen(row.get("selected_results") or [])
        )
        for row in rows
        if row.get("eval_id") in wanted
    }
    missing = wanted - set(hashes)
    if missing:
        raise ValueError("phase3 sentinel rows missing from frozen bundle")
    return hashes


def run_cp_a0_probe(
    *,
    frozen_tag: str = PHASE3_FROZEN_TAG,
    strategy_override: str | None = None,
) -> dict[str, Any]:
    """Run the locked live non-holdout reproducibility probe.

    The returned artifact is aggregate-only by design: it exposes counts and the
    binding bridge decision, not per-row hashes.
    """
    rows = load_eval_dataset(
        splits=("regression", "dev"),
        row_ids=list(CP_A0_SENTINELS),
    )
    if [row["id"] for row in rows] != list(CP_A0_SENTINELS):
        rows_by_id = {row["id"]: row for row in rows}
        rows = [rows_by_id[eval_id] for eval_id in CP_A0_SENTINELS]
    frozen_hashes = _phase3_hashes(frozen_tag)
    policy = resolve_policy().policy
    policy = replace(
        policy,
        retrieval_defaults=replace(
            policy.retrieval_defaults,
            adaptive_context_enabled=False,
        ),
    )
    matches = 0
    for item in rows:
        collector = TraceCollector(capture_candidate_stages=True)
        state = AnswerState(item["question"], debug_enabled=True, policy=policy)
        trace_id = new_trace_id()
        with trace_context(trace_id=trace_id, collector=collector):
            prepare_kwargs: dict[str, Any] = {}
            if strategy_override is not None:
                prepare_kwargs["strategy_override"] = strategy_override
            prepare_answer_state(state, **prepare_kwargs)
        live_hash = packaging_pool_full_hash(state.selection.selected)
        if live_hash == frozen_hashes[item["id"]]:
            matches += 1
    all_match = matches == len(CP_A0_SENTINELS)
    return {
        "checkpoint": "CP-A0",
        "created_at": datetime.now().astimezone().isoformat(),
        "frozen_tag": frozen_tag,
        "sentinel_count": len(CP_A0_SENTINELS),
        "matched_count": matches,
        "mismatched_count": len(CP_A0_SENTINELS) - matches,
        "bridge": "full-path" if all_match else "purity+invariant",
    }


def _semantic_selection_signature(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.retriever.types import RetrievalResult

    return [
        selector_semantic_record(
            RetrievalResult(
                chunk_id=str(item["chunk_id"]),
                text=str(item.get("text", "")),
                score=float(item.get("score", 0.0)),
                metadata=dict(item.get("metadata") or {}),
            )
        )
        for item in selected
    ]


def run_cp_a2c_semantic_probe(
    *,
    frozen_tag: str = "phase4-adaptive-context-v2-minilm",
) -> dict[str, Any]:
    """Validate live-on selection against sealed v2 at selector-semantic granularity."""
    from app.evals.integrity import sha256

    rows = load_eval_dataset(splits=("regression", "dev"))
    sealed_rows, _meta = validate_sealed_bundle(paths_for(frozen_tag))
    sealed_by_id = {row["eval_id"]: row for row in sealed_rows}
    expected_by_id = {
        row["eval_id"]: sha256(_semantic_selection_signature(row["selected_results"]))
        for row in sealed_rows
    }
    missing = {row["id"] for row in rows} - set(expected_by_id)
    if missing:
        raise ValueError("sealed v2 rows missing from frozen bundle")
    policy = resolve_policy().policy
    policy = replace(
        policy,
        retrieval_defaults=replace(
            policy.retrieval_defaults,
            adaptive_context_enabled=True,
        ),
    )
    failures = 0
    full_hash_mismatches = 0
    for item in rows:
        collector = TraceCollector(capture_candidate_stages=True)
        state = AnswerState(item["question"], debug_enabled=True, policy=policy)
        with trace_context(trace_id=new_trace_id(), collector=collector):
            prepare_answer_state(state)
        selected = [
            {
                "chunk_id": result.chunk_id,
                "text": result.text,
                "score": result.score,
                "metadata": dict(result.metadata),
            }
            for result in state.selection.selected
        ]
        observed_semantic = sha256(_semantic_selection_signature(selected))
        if observed_semantic != expected_by_id[item["id"]]:
            failures += 1
        if sha256(selected) != sealed_by_id[item["id"]]["selected_context_hash"]:
            full_hash_mismatches += 1
    return {
        "checkpoint": "CP-A2.c-semantic",
        "created_at": datetime.now().astimezone().isoformat(),
        "frozen_tag": frozen_tag,
        "rows": len(rows),
        "semantic_failures": failures,
        "semantic_matches": len(rows) - failures,
        "score_inclusive_full_hash_mismatches": full_hash_mismatches,
        "status": "passed" if failures == 0 else "failed",
    }
