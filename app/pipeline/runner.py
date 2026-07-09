import json
import time
from datetime import datetime, timezone

from app.config import settings
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.observability.logger import get_logger
from app.observability.trace import TraceWriter
from app.pipeline.policy import AnswerPolicy, resolve_policy
from app.pipeline import stages
from app.pipeline.stages import _chunk_trace
from app.pipeline.state import AnswerState
from app.retriever.prompts import is_conversational

logger = get_logger(__name__)


def _feature_flags(policy: AnswerPolicy) -> dict:
    return {
        "debug": settings.debug,
        "trace_logging_enabled": settings.trace_logging_enabled,
        "edge_expansion_enabled": policy.retrieval_defaults.edge_expansion_enabled,
        "answerability_gate_enabled": policy.evidence_gate == "answerability",
        "query_decomposition_enabled": policy.query_decomposition_enabled,
        "subquery_packaging_enabled": policy.retrieval_defaults.subquery_packaging_enabled,
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
        "latency_ms": round(elapsed_ms, 2),
        "prompt_length": len(state.prompt) if state.prompt else 0,
        "generator_model": policy.generator_model,
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
    want_record = state.debug_enabled or (trace and settings.trace_logging_enabled)
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
        TraceWriter().write(trace_record)
    return trace_record


def run_answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    strategy_override: str | None = None,
) -> tuple[dict, dict | None]:
    trace_id = new_trace_id()
    started = time.perf_counter()
    debug_enabled = settings.debug if debug is None else debug
    collector = TraceCollector() if (trace and settings.trace_logging_enabled) or debug_enabled else None
    policy = resolve_policy().policy
    state = AnswerState(
        question=question,
        debug_enabled=debug_enabled,
        session_id=session_id,
        policy=policy,
    )

    with trace_context(trace_id=trace_id, session_id=session_id, collector=collector):
        logger.info("answer_started", trace_label=trace_label)
        _ensure_session(session_id)

        if is_conversational(question):
            stages.package_greeting(state)
        else:
            stages.rewrite_query(state)
            stages.classify_intent(state, strategy_override=strategy_override)
            stages.plan_retrieval(state)
            stages.retrieve_context(state)
            stages.gate_evidence(state)
            if state.response is None:
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
