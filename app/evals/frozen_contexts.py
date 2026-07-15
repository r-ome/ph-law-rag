"""Retrieval-process capture and publication of frozen-context bundles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.evals.integrity import (
    FrozenPaths,
    _pre_rerank_pool_hash,
    atomic_write_json,
    file_sha256,
    ordered_hash,
    query_separation_identity,
    schema_version,
    sha256,
    text_sha256,
)
from app.pipeline.frozen_generation import prepare_prompts
from app.pipeline.state import AnswerState
from app.retriever.types import RetrievalResult


def _result(result: RetrievalResult) -> dict[str, Any]:
    return {
        "chunk_id": result.chunk_id,
        "text": result.text,
        "score": result.score,
        # Metadata is intentionally verbatim: build_context consumes it for
        # source/citation filtering during replay.
        "metadata": dict(result.metadata),
    }


def _selection(state: AnswerState) -> dict[str, list[dict[str, Any]]]:
    return {
        "retrieved": [_result(result) for result in state.selection.retrieved],
        "pre_expansion": [_result(result) for result in state.selection.pre_expansion],
        "selected": [_result(result) for result in state.selection.selected],
    }


def _legal_query_separation(state: AnswerState) -> dict[str, Any]:
    source_query = state.effective_question or state.question
    identity = query_separation_identity(arm=state.query_separation_arm)
    decision = state.legal_rewrite_decision
    if decision is None:
        decision_record: dict[str, Any] = {
            "status": "disabled",
            "legal_query": None,
            "legal_query_hash": None,
            "confidence": None,
            "parser_outcome": "not_called",
            "fallback_reason": "disabled",
            "model": None,
            "prompt_version": identity["prompt_version"],
            "prompt_hash": identity["prompt_hash"],
            "raw_output_hash": None,
            "call_latency_ms": None,
            "cache_key": None,
            "cache_status": "bypassed",
        }
    else:
        decision_record = {
            "status": decision.status,
            "legal_query": decision.legal_query,
            "legal_query_hash": (
                text_sha256(decision.legal_query)
                if decision.legal_query is not None
                else None
            ),
            "confidence": decision.confidence,
            "parser_outcome": decision.parser_outcome,
            "fallback_reason": decision.fallback_reason,
            "model": decision.model,
            "prompt_version": decision.prompt_version,
            "prompt_hash": decision.prompt_hash,
            "raw_output_hash": decision.raw_output_hash,
            "call_latency_ms": decision.call_latency_ms,
            "cache_key": decision.cache_key,
            "cache_status": decision.cache_status,
        }
    source_query_hash = text_sha256(source_query)
    semantic_decision = {
        key: value
        for key, value in decision_record.items()
        if key not in {"call_latency_ms", "cache_status"}
    }
    semantic_input_hash = sha256(
        {
            "arm": state.query_separation_arm,
            "source_query": source_query,
            "source_query_hash": source_query_hash,
            "decision": semantic_decision,
        }
    )
    return {
        "arm": state.query_separation_arm,
        "source_query": source_query,
        "source_query_hash": source_query_hash,
        "decision": decision_record,
        "semantic_input_hash": semantic_input_hash,
    }


def make_record(state: AnswerState, trace_record: dict[str, Any]) -> dict[str, Any]:
    selection = _selection(state)
    selected = selection["selected"]
    policy = state.policy
    candidate_stages = list(trace_record.get("candidate_stages", []))
    retrieval_trace = {
        key: value for key, value in trace_record.items() if key != "candidate_stages"
    }
    terminal_response = dict(state.response) if state.response is not None else None
    schema = schema_version()
    record: dict[str, Any] = {
        "schema": schema,
        "eval_id": state.question,  # replaced with the stable dataset ID by the runner
        "question": state.question,
        "effective_question": state.effective_question or state.question,
        "selection": selection,
        "selected_results": selected,
        "selected_context_hash": sha256(selected),
        "pre_rerank_pool_hash": _pre_rerank_pool_hash(
            candidate_stages,
            schema_minor=schema["minor"],
        ),
        "candidate_stages": candidate_stages,
        "legal_query_separation": _legal_query_separation(state),
        "retrieval_trace": retrieval_trace,
        "evidence": state.evidence.as_trace_dict() if state.evidence else None,
        "corrective_retrieval": {
            "ran": state.corrective_ran,
            "added_chunks": state.corrective_added_chunks,
            "baseline_selected_count": state.corrective_baseline_selected_count,
            "post_selected_count": state.corrective_post_selected_count,
            "max_added": state.corrective_max_added,
        },
        "terminal_response": terminal_response,
        "hard_abstention": bool(terminal_response and terminal_response.get("abstained")),
        "model_choice": state.model_choice.as_trace_dict() if state.model_choice else None,
        "policy": policy.as_trace_dict() if policy else {},
    }
    if terminal_response is None:
        results = [
            RetrievalResult(
                chunk_id=item["chunk_id"],
                text=item["text"],
                score=item["score"],
                metadata=dict(item["metadata"]),
            )
            for item in selected
        ]
        context, sources, system, user = prepare_prompts(
            record["effective_question"],
            results,
            later_enacted_preference=bool(
                policy and policy.later_enacted_preference_enabled
            ),
        )
        record.update(
            {
                "context_block_hash": text_sha256(context),
                "source_map": sources,
                "source_map_hash": sha256(sources),
                "system_prompt_hash": text_sha256(system),
                "user_prompt_hash": text_sha256(user),
                "system_prompt": system,
                "user_prompt": user,
            }
        )
    return record


def _write_derived_retrieval_artifacts(
    paths: FrozenPaths,
    rows: list[dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
) -> None:
    from app.evals.retrieval_metrics import build_retrieval_summary
    from app.evals.retrieval_trace import (
        append_completed_row,
        candidate_count_metadata,
        candidate_lines,
    )

    temporary = paths.trace.with_name(paths.trace.name + ".tmp")
    temporary.unlink(missing_ok=True)
    for row in rows:
        response = row.get("terminal_response") or {
            "abstained": False,
            "context_sources": [
                item.get("metadata", {}).get("source_id", "")
                for item in row["selected_results"]
            ],
        }
        item = {
            "id": row["eval_id"],
            "question": row["question"],
            "category": row.get("category"),
            "split": row.get("split"),
        }
        trace_record = row["retrieval_trace"]
        target = targets_by_id.get(row["eval_id"])
        lines = candidate_lines(
            item,
            response,
            {**trace_record, "candidate_stages": row["candidate_stages"]},
            target,
        )
        candidate_count, stage_counts, variant_counts = candidate_count_metadata(
            row["candidate_stages"]
        )
        append_completed_row(
            temporary,
            row["eval_id"],
            lines,
            retrieval_latency_ms=trace_record.get("retrieval_latency_ms", 0.0),
            abstained=bool(response.get("abstained")),
            category=row.get("category"),
            target_record=target,
            candidate_count=candidate_count,
            stage_candidate_counts=stage_counts,
            stage_candidate_counts_by_query_variant=variant_counts,
            stage_timings_ms=trace_record.get("retrieval_stage_timings_ms", {}),
        )
    with temporary.open("ab") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, paths.trace)
    atomic_write_json(paths.summary, build_retrieval_summary(paths.trace, holdout=False))


def seal(
    paths: FrozenPaths,
    *,
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
    targets_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not paths.partial.exists():
        raise ValueError("cannot seal a retrieval bundle without its partial JSONL")
    _write_derived_retrieval_artifacts(paths, rows, targets_by_id)
    publication = {
        "row_count": len(rows),
        "eval_ids": [row["eval_id"] for row in rows],
        "ordered_record_hash": ordered_hash([row["record_hash"] for row in rows]),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [
                {"eval_id": row["eval_id"], "hash": row.get("pre_rerank_pool_hash")}
                for row in rows
            ]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [
                {"eval_id": row["eval_id"], "hash": row["selected_context_hash"]}
                for row in rows
            ]
        ),
        "ordered_legal_query_separation_semantic_input_hash": ordered_hash(
            [
                {
                    "eval_id": row["eval_id"],
                    "hash": row["legal_query_separation"]["semantic_input_hash"],
                }
                for row in rows
            ]
        ),
        "bundle_file_hash": file_sha256(paths.partial),
        "retrieval_trace_hash": file_sha256(paths.trace),
        "retrieval_summary_hash": file_sha256(paths.summary),
    }
    final_meta = {
        "schema": schema_version(),
        "artifact_type": "retrieval_bundle",
        **meta,
        **publication,
    }
    atomic_write_json(paths.meta, final_meta)
    os.replace(paths.partial, paths.sealed)
    atomic_write_json(
        paths.state,
        {"schema": schema_version(), "state": "sealed", **publication},
    )
    return final_meta
