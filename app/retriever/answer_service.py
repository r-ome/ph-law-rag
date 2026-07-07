import time
from datetime import datetime, timezone

from app.retriever.context_builder import build_context
from app.retriever.prompts import (
    SYSTEM_PROMPT, LATER_ENACTED_RULE, ABSTAIN_MESSAGE, GREETING_MESSAGE,
    is_abstention, is_conversational, build_user_prompt
)
from app.retriever.llm_client import generate, LLMError
from app.retriever.types import RetrievalResult
from app.retriever.answerability import is_answerable
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import STRATEGIES, RetrievalKnobs, resolve_knobs
from app.config import settings
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.observability.logger import get_logger
from app.observability.trace import TraceWriter

logger = get_logger(__name__)


def _chunk_trace(r: RetrievalResult, preview_chars: int) -> dict:
    return {
        "chunk_id": r.chunk_id,
        "score": r.score,
        "source_id": r.metadata.get("source_id", ""),
        "unit_label": r.metadata.get("unit_label", ""),
        "provision_id": r.metadata.get("provision_id", ""),
        "expanded_from_parent": bool(r.metadata.get("expanded_from_parent")),
        "consolidated": r.metadata.get("consolidated", ""),
        "dedup_merged_chunk_ids": r.metadata.get("dedup_merged_chunk_ids", []),
        "preview": r.text[:preview_chars],
    }

def _debug_trace(
    retrieved: list[RetrievalResult],
    pre_expansion: list[RetrievalResult],
    selected: list[RetrievalResult],
    prompt: str | None,
    preview_chars: int = 120,
) -> dict:
    return {
        "num_retrieved": len(retrieved),
        "num_pre_expansion": len(pre_expansion),
        "num_reranked": len(pre_expansion),
        "num_selected": len(selected),
        "prompt_length": len(prompt) if prompt else 0,
        "pre_expansion_chunks": [
            _chunk_trace(r, preview_chars)
            for r in pre_expansion
        ],
        "chunks": [
            _chunk_trace(r, preview_chars)
            for r in selected
        ],
    }


def _feature_flags() -> dict:
    return {
        "debug": settings.debug,
        "trace_logging_enabled": settings.trace_logging_enabled,
        "edge_expansion_enabled": settings.edge_expansion_enabled,
        "answerability_gate_enabled": settings.answerability_gate_enabled,
        "query_decomposition_enabled": settings.query_decomposition_enabled,
        "subquery_packaging_enabled": settings.subquery_packaging_enabled,
        "enable_query_rewriting": settings.enable_query_rewriting,
        "faithfulness_selfcheck_enabled": settings.faithfulness_selfcheck_enabled,
        "later_enacted_preference_enabled": settings.later_enacted_preference_enabled,
        "reranker_backend": settings.reranker_backend,
        "min_chunks_for_answer": settings.min_chunks_for_answer,
    }


def _build_trace_record(
    *,
    trace_id: str,
    trace_label: str | None,
    session_id: str | None,
    original_question: str,
    rewritten_question: str,
    response: dict,
    selection: SelectionResult,
    prompt: str | None,
    collector: TraceCollector | None,
    elapsed_ms: float,
    strategy_name: str,
    strategy_knobs: RetrievalKnobs,
) -> dict:
    preview_chars = settings.trace_max_text_preview
    return {
        "trace_id": trace_id,
        "trace_label": trace_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "question": original_question,
        "rewritten_question": rewritten_question,
        "stage_counts": {
            "retrieved": len(selection.retrieved),
            "pre_expansion": len(selection.pre_expansion),
            "selected": len(selection.selected),
        },
        "retrieved_chunks": [_chunk_trace(r, preview_chars) for r in selection.retrieved],
        "pre_expansion_chunks": [_chunk_trace(r, preview_chars) for r in selection.pre_expansion],
        "selected_chunks": [_chunk_trace(r, preview_chars) for r in selection.selected],
        "retrieval_strategy": {
            "strategy": strategy_name,
            "knobs": strategy_knobs.as_trace_dict(),
        },
        "feature_flags": _feature_flags(),
        "abstained": response.get("abstained", False),
        "error": response.get("error", False),
        "stages": list(collector.stages) if collector else [],
        "latency_ms": round(elapsed_ms, 2),
        "prompt_length": len(prompt) if prompt else 0,
        "generator_model": settings.llm_model,
    }

def _package(
    answer: str,
    sources: list[dict],
    abstained: bool,
    selection: SelectionResult,
    prompt: str | None,
    error: bool = False,
    debug: bool = False
) -> dict:
    response = {
        "answer": answer,
        "sources": sources,
        "contexts": [r.text for r in selection.selected],
        "context_sources": [r.metadata.get("source_id", "") for r in selection.selected],
        "abstained": abstained,
        "error": error
    }
    if debug:
        response["debug"] = _debug_trace(
            selection.retrieved,
            selection.pre_expansion,
            selection.selected,
            prompt,
        )
    return response


def _run_pipeline(
    question: str,
    debug_enabled: bool,
    strategy_name: str,
    strategy_knobs: RetrievalKnobs,
) -> tuple[dict, SelectionResult, str | None]:
    selection = STRATEGIES[strategy_name].execute(question, knobs=strategy_knobs)

    if len(selection.pre_expansion) < settings.min_chunks_for_answer:
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=selection,
            prompt=None,
            debug=debug_enabled
        ), selection, None

    if settings.answerability_gate_enabled and not is_answerable(question, selection.pre_expansion):
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=selection,
            prompt=None,
            debug=debug_enabled
        ), selection, None

    context_block, sources = build_context(selection.selected)

    user_prompt = build_user_prompt(question, context_block)
    system_prompt = SYSTEM_PROMPT
    if settings.later_enacted_preference_enabled:
        system_prompt = SYSTEM_PROMPT + LATER_ENACTED_RULE
    try:
        answer_text = generate(system_prompt, user_prompt)
    except LLMError as e:
        logger.warning("generation_failed", error=str(e), model=settings.llm_model)
        return _package(
            f"The language model could not be reached: {e}",
            sources=[],
            abstained=False,
            selection=selection,
            prompt=user_prompt,
            error=True,
            debug=debug_enabled
        ), selection, user_prompt

    # Faithfulness self-check (groundedness lever): a 2nd local pass that strips
    # any claim not supported by the same context. Skip when the draft already
    # abstained — nothing to audit. Keep the draft if the pass fails or empties.
    if settings.faithfulness_selfcheck_enabled and not is_abstention(answer_text):
        from app.retriever.prompts import SELFCHECK_SYSTEM, build_selfcheck_prompt
        try:
            revised = generate(
                SELFCHECK_SYSTEM,
                build_selfcheck_prompt(question, context_block, answer_text),
            )
            if revised.strip():
                answer_text = revised
        except LLMError:
            pass  # self-check is best-effort; fall back to the draft

    soft_abstained = is_abstention(answer_text)

    return _package(
        answer_text,
        sources=[] if soft_abstained else sources,
        abstained=soft_abstained,
        selection=selection,
        prompt=user_prompt,
        debug=debug_enabled
    ), selection, user_prompt


def answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    ) -> dict:
    trace_id = new_trace_id()
    collector = TraceCollector() if trace and settings.trace_logging_enabled else None
    started = time.perf_counter()
    debug_enabled = settings.debug if debug is None else debug
    effective_question = question
    prompt: str | None = None
    selection = SelectionResult(retrieved=[], pre_expansion=[], selected=[])
    strategy_name = "default"
    strategy_knobs = resolve_knobs(strategy_name)

    with trace_context(trace_id=trace_id, session_id=session_id, collector=collector):
        logger.info("answer_started", trace_label=trace_label)

        if session_id:
            from app.conversation.session import session_exists, create_session
            if not session_exists(session_id):
                create_session(session_id=session_id)  # guard direct callers (FK safety)

        if is_conversational(question):
            # Greeting / chitchat — reply conversationally, skip retrieval entirely so
            # we never dump unrelated context for a non-legal message.
            response = _package(
                GREETING_MESSAGE,
                sources=[],
                abstained=False,
                selection=selection,
                prompt=None,
                debug=debug_enabled,
            )
        else:
            if session_id:
                from app.conversation.session import get_history
                from app.conversation.query_rewriter import rewrite_query
                history = get_history(session_id, settings.max_conversation_turns)
                effective_question = rewrite_query(question, history)
            response, selection, prompt = _run_pipeline(
                effective_question,
                debug_enabled,
                strategy_name,
                strategy_knobs,
            )

        if session_id:
            import json
            from app.conversation.session import append_turn
            append_turn(session_id, {
                "question": question,                       # original, not rewritten
                "rewritten_question": effective_question,
                "answer": response["answer"],
                "retrieved_chunks_json": json.dumps([r.chunk_id for r in selection.selected]),
            })

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "answer_completed",
            trace_label=trace_label,
            abstained=response.get("abstained", False),
            error=response.get("error", False),
            retrieved=len(selection.retrieved),
            selected=len(selection.selected),
            latency_ms=round(elapsed_ms, 2),
        )
        if trace and settings.trace_logging_enabled:
            TraceWriter().write(
                _build_trace_record(
                    trace_id=trace_id,
                    trace_label=trace_label,
                    session_id=session_id,
                    original_question=question,
                    rewritten_question=effective_question,
                    response=response,
                    selection=selection,
                    prompt=prompt,
                    collector=collector,
                    elapsed_ms=elapsed_ms,
                    strategy_name=strategy_name,
                    strategy_knobs=strategy_knobs,
                )
            )
        return response
