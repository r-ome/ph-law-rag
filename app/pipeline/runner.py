import json
import time
from dataclasses import fields as dataclass_fields, replace
from datetime import datetime, timezone

from app.config import settings
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.observability.logger import get_logger
from app.observability.trace import TraceWriter
from app.pipeline.policy import AnswerPolicy, resolve_policy
from app.pipeline import stages
from app.pipeline.stages import _chunk_trace
from app.pipeline.state import AnswerState, LegalQuerySeparationArm
from app.retriever.prompts import is_conversational

logger = get_logger(__name__)

_SELECTION_TIMER_STAGES = {
    "edge_expansion",
    "prefer_operative",
    "parent_expansion",
    "sibling_expansion",
    "dedup",
    "adaptive_context",
}


def _retrieval_latency_ms(collector: TraceCollector | None) -> float:
    if collector is None:
        return 0.0
    names = {stage.get("name") for stage in collector.stages}
    base_names = (
        {"packaged_retrieve"}
        if "packaged_retrieve" in names
        else {"hybrid_retriever", "rerank"}
    )
    included = base_names | _SELECTION_TIMER_STAGES | {"corrective_retrieval"}
    return round(
        sum(
            float(stage.get("ms", 0.0))
            for stage in collector.stages
            if stage.get("name") in included
        ),
        2,
    )


def _retrieval_stage_timings_ms(collector: TraceCollector | None) -> dict[str, float]:
    if collector is None:
        return {}
    groups = {
        "dense": {"dense_retriever"},
        "sparse": {"sparse_retriever"},
        "fusion": {"fusion"},
        "reranked": {"rerank_scoring"},
        "expanded": {
            "edge_expansion",
            "prefer_operative",
            "parent_expansion",
            "sibling_expansion",
        },
        "sibling_expansion": {"sibling_expansion"},
        "selected": {"dedup"},
        "adaptive_context": {"adaptive_context"},
        "corrective": {"corrective_retrieval"},
    }
    return {
        group: round(
            sum(
                float(stage.get("ms", 0.0))
                for stage in collector.stages
                if stage.get("name") in names
            ),
            2,
        )
        for group, names in groups.items()
        if any(stage.get("name") in names for stage in collector.stages)
    }


def _feature_flags(policy: AnswerPolicy) -> dict:
    return {
        "debug": settings.debug,
        "trace_logging_enabled": settings.trace_logging_enabled,
        "edge_expansion_enabled": policy.retrieval_defaults.edge_expansion_enabled,
        "sibling_expansion_enabled": policy.retrieval_defaults.sibling_expansion_enabled,
        "answerability_gate_enabled": policy.evidence_gate == "answerability",
        "evidence_gate": policy.evidence_gate,
        "evidence_judge_model": policy.evidence_judge_model,
        "corrective_retrieval_enabled": policy.corrective_retrieval_enabled,
        "query_decomposition_enabled": policy.query_decomposition_enabled,
        "subquery_packaging_enabled": policy.retrieval_defaults.subquery_packaging_enabled,
        "adaptive_context_enabled": policy.retrieval_defaults.adaptive_context_enabled,
        "adaptive_context_contract_version": policy.retrieval_defaults.adaptive_context_contract_version,
        "adaptive_context_floor": policy.retrieval_defaults.adaptive_context_floor,
        "adaptive_context_base_cap": policy.retrieval_defaults.adaptive_context_base_cap,
        "adaptive_context_uncertain_cap": policy.retrieval_defaults.adaptive_context_uncertain_cap,
        "adaptive_context_multifacet_cap": policy.retrieval_defaults.adaptive_context_multifacet_cap,
        "adaptive_context_stabilization_patience": (
            policy.retrieval_defaults.adaptive_context_stabilization_patience
        ),
        "adaptive_context_token_target": policy.retrieval_defaults.adaptive_context_token_target,
        "adaptive_context_token_estimator": policy.retrieval_defaults.adaptive_context_token_estimator,
        "enable_query_rewriting": policy.query_rewriting_enabled,
        "faithfulness_selfcheck_enabled": policy.selfcheck_enabled,
        "later_enacted_preference_enabled": policy.later_enacted_preference_enabled,
        "reranker_backend": settings.reranker_backend,
        "min_chunks_for_answer": policy.min_chunks_for_answer,
    }


def _build_trace_record(
    *,
    trace_id: str,
    trace_label: str | None,
    state: AnswerState,
    collector: TraceCollector | None,
    elapsed_ms: float,
) -> dict:
    preview_chars = settings.trace_max_text_preview
    response = state.response or {}
    policy = state.policy or resolve_policy().policy
    return {
        "trace_id": trace_id,
        "trace_label": trace_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": state.session_id,
        "question": state.question,
        "rewritten_question": state.effective_question,
        "stage_counts": {
            "retrieved": len(state.selection.retrieved),
            "pre_expansion": len(state.selection.pre_expansion),
            "selected": len(state.selection.selected),
        },
        "retrieved_chunks": [_chunk_trace(r, preview_chars) for r in state.selection.retrieved],
        "pre_expansion_chunks": [
            _chunk_trace(r, preview_chars) for r in state.selection.pre_expansion
        ],
        "selected_chunks": [_chunk_trace(r, preview_chars) for r in state.selection.selected],
        "retrieval_strategy": {
            "strategy": state.strategy_name,
            "knobs": state.strategy_knobs.as_trace_dict(),
        },
        "intent_router": {
            "enabled": policy.router_enabled,
            "model": policy.router_model if policy.router_enabled else None,
            "decision": (
                state.router_decision.as_trace_dict() if state.router_decision else None
            ),
            **(
                {"skipped_reason": state.router_skipped_reason}
                if state.router_skipped_reason
                else {}
            ),
        },
        "feature_flags": _feature_flags(policy),
        "profile": policy.name,
        "policy": policy.as_trace_dict(),
        "abstained": response.get("abstained", False),
        "error": response.get("error", False),
        "stages": list(collector.stages) if collector else [],
        **(
            {"candidate_stages": list(collector.candidate_stages)}
            if collector and collector.capture_candidate_stages
            else {}
        ),
        "retrieval_latency_ms": _retrieval_latency_ms(collector),
        "retrieval_stage_timings_ms": _retrieval_stage_timings_ms(collector),
        "latency_ms": round(elapsed_ms, 2),
        "prompt_length": len(state.prompt) if state.prompt else 0,
        "generator_model": (
            state.model_choice.model if state.model_choice else policy.generator_model
        ),
        "model_choice": (
            state.model_choice.as_trace_dict() if state.model_choice else None
        ),
        "evidence": state.evidence.as_trace_dict() if state.evidence else None,
        "corrective_retrieval": {
            "enabled": policy.corrective_retrieval_enabled,
            "fired": state.corrective_ran,
            "added_chunks": state.corrective_added_chunks,
            "baseline_selected_count": (
                state.corrective_baseline_selected_count
                if state.corrective_baseline_selected_count is not None
                else len(state.selection.selected)
            ),
            "post_selected_count": (
                state.corrective_post_selected_count
                if state.corrective_post_selected_count is not None
                else len(state.selection.selected)
            ),
            "max_added": (
                state.corrective_max_added
                if state.corrective_max_added is not None
                else policy.retrieval_defaults.subquery_reserve_n
            ),
        },
    }


def _ensure_session(session_id: str | None) -> None:
    if not session_id:
        return
    from app.conversation.session import create_session, session_exists

    if not session_exists(session_id):
        create_session(session_id=session_id)


def _append_session_turn(state: AnswerState) -> None:
    if not state.session_id or state.response is None:
        return
    from app.conversation.session import append_turn

    append_turn(
        state.session_id,
        {
            "question": state.question,
            "rewritten_question": state.effective_question,
            "answer": state.response["answer"],
            "retrieved_chunks_json": json.dumps([r.chunk_id for r in state.selection.selected]),
            "sources_json": json.dumps(state.response.get("sources", [])),
        },
    )


def _attach_debug_stages(state: AnswerState, collector: TraceCollector | None) -> None:
    if state.debug_enabled and collector and state.response is not None:
        state.response.setdefault("debug", {})["stages"] = list(collector.stages)


def _attach_model_metadata(state: AnswerState) -> None:
    if state.response is None or state.model_choice is None:
        return
    state.response["model_choice"] = state.model_choice.as_trace_dict()
    state.response["generator_model"] = state.model_choice.model


def _attach_corrective_metadata(state: AnswerState) -> None:
    if state.response is None:
        return
    policy = state.policy or resolve_policy().policy
    state.response["corrective_retrieval"] = {
        "enabled": policy.corrective_retrieval_enabled,
        "fired": state.corrective_ran,
        "added_chunks": state.corrective_added_chunks,
        "baseline_selected_count": (
            state.corrective_baseline_selected_count
            if state.corrective_baseline_selected_count is not None
            else len(state.selection.selected)
        ),
        "post_selected_count": (
            state.corrective_post_selected_count
            if state.corrective_post_selected_count is not None
            else len(state.selection.selected)
        ),
        "max_added": (
            state.corrective_max_added
            if state.corrective_max_added is not None
            else policy.retrieval_defaults.subquery_reserve_n
        ),
    }


def _attach_evidence_metadata(state: AnswerState) -> None:
    if state.response is None:
        return
    state.response["evidence"] = state.evidence.as_trace_dict() if state.evidence else None


def _finalize(
    *,
    state: AnswerState,
    trace_id: str,
    trace_label: str | None,
    trace: bool,
    collector: TraceCollector | None,
    started: float,
) -> dict | None:
    if state.response is None:
        return None

    _attach_model_metadata(state)
    _attach_corrective_metadata(state)
    _attach_evidence_metadata(state)
    _attach_debug_stages(state, collector)
    _append_session_turn(state)

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "answer_completed",
        trace_label=trace_label,
        abstained=state.response.get("abstained", False),
        error=state.response.get("error", False),
        retrieved=len(state.selection.retrieved),
        selected=len(state.selection.selected),
        latency_ms=round(elapsed_ms, 2),
    )
    want_record = (
        state.debug_enabled
        or (trace and settings.trace_logging_enabled)
        or bool(collector and collector.capture_candidate_stages)
    )
    if not want_record:
        return None

    trace_record = _build_trace_record(
        trace_id=trace_id,
        trace_label=trace_label,
        state=state,
        collector=collector,
        elapsed_ms=elapsed_ms,
    )
    if trace and settings.trace_logging_enabled:
        operational_record = dict(trace_record)
        # Candidate snapshots are eval artifacts only. Keeping them out of the
        # operational trace writer also keeps them off the existing trace API.
        operational_record.pop("candidate_stages", None)
        TraceWriter().write(operational_record)
    return trace_record


def run_answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    strategy_override: str | None = None,
    policy_overrides: dict | None = None,
    capture_candidate_stages: bool = False,
) -> tuple[dict, dict | None]:
    trace_id = new_trace_id()
    started = time.perf_counter()
    debug_enabled = settings.debug if debug is None else debug
    collector = (
        TraceCollector(capture_candidate_stages=capture_candidate_stages)
        if (trace and settings.trace_logging_enabled) or debug_enabled or capture_candidate_stages
        else None
    )
    policy = resolve_policy().policy
    if policy_overrides:
        policy_field_names = {f.name for f in dataclass_fields(AnswerPolicy)}
        knob_field_names = {f.name for f in dataclass_fields(type(policy.retrieval_defaults))}
        direct = {k: v for k, v in policy_overrides.items() if k in policy_field_names}
        knobs = {
            k: v for k, v in policy_overrides.items()
            if k in knob_field_names and k not in policy_field_names
        }
        if direct:
            policy = replace(policy, **direct)
        if knobs:
            policy = replace(
                policy, retrieval_defaults=replace(policy.retrieval_defaults, **knobs)
            )
    state = AnswerState(
        question=question,
        debug_enabled=debug_enabled,
        session_id=session_id,
        policy=policy,
    )

    with trace_context(trace_id=trace_id, session_id=session_id, collector=collector):
        logger.info("answer_started", trace_label=trace_label)
        _ensure_session(session_id)

        prepare_answer_state(state, strategy_override=strategy_override)
        if state.response is None:
            stages.route_model(state)
            stages.generate_answer(state)

        trace_record = _finalize(
            state=state,
            trace_id=trace_id,
            trace_label=trace_label,
            trace=trace,
            collector=collector,
            started=started,
        )
    return state.response, trace_record


def prepare_answer_state(
    state: AnswerState,
    *,
    strategy_override: str | None = None,
    query_separation_arm: LegalQuerySeparationArm = "original_only",
) -> AnswerState:
    """Run the production preparation sequence, stopping before generation."""
    if query_separation_arm not in {"original_only", "original_plus_rewrite"}:
        raise ValueError(
            f"unsupported query-separation arm {query_separation_arm!r}"
        )
    state.query_separation_arm = query_separation_arm
    if is_conversational(state.question):
        stages.package_greeting(state)
        return state
    stages.rewrite_query(state)
    stages.classify_intent(state, strategy_override=strategy_override)
    stages.plan_retrieval(state)
    stages.prepare_legal_query_separation(state)
    stages.retrieve_context(state)
    stages.gate_evidence(state)
    if state.response is None:
        if (
            state.evidence is not None
            and state.evidence.verdict == "partial"
            and state.policy is not None
            and state.policy.corrective_retrieval_enabled
        ):
            stages.corrective_retrieve(state)
    return state


def answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    strategy_override: str | None = None,
) -> dict:
    """Backward-compatible wrapper - public return is unchanged (CLI + evals)."""
    return run_answer(
        question,
        debug=debug,
        session_id=session_id,
        trace=trace,
        trace_label=trace_label,
        strategy_override=strategy_override,
    )[0]
