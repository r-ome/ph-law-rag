import re

from app.config import settings
from app.observability.logger import get_logger
from app.retriever.context_builder import build_context
from app.retriever.context_selection import SelectionResult
from app.retriever.llm_client import LLMError, generate
from app.retriever.prompts import (
    ABSTAIN_MESSAGE,
    GREETING_MESSAGE,
    LATER_ENACTED_RULE,
    SYSTEM_PROMPT,
    build_user_prompt,
    is_abstention,
)
from app.retriever.strategy import STRATEGIES, resolve_knobs
from app.retriever.types import RetrievalResult

from app.pipeline.state import AnswerState

logger = get_logger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _cited_sources(answer_text: str, sources: list[dict]) -> list[dict]:
    cited_refs = {int(match.group(1)) for match in _CITATION_RE.finditer(answer_text)}
    if not cited_refs:
        return []
    return [source for source in sources if source.get("ref") in cited_refs]


def _chunk_trace(r: RetrievalResult, preview_chars: int) -> dict:
    consolidated = r.metadata.get("consolidated", "")
    return {
        "chunk_id": r.chunk_id,
        "score": r.score,
        "source_id": r.metadata.get("source_id", ""),
        "unit_label": r.metadata.get("unit_label", ""),
        "provision_id": r.metadata.get("provision_id", ""),
        "expanded_from_parent": bool(r.metadata.get("expanded_from_parent")),
        "expanded_from_sibling": bool(r.metadata.get("expanded_from_sibling")),
        "sibling_seed_chunk_id": r.metadata.get("sibling_seed_chunk_id", ""),
        "sibling_offset": r.metadata.get("sibling_offset"),
        "consolidated": "" if consolidated is None else str(consolidated),
        "dedup_merged_chunk_ids": r.metadata.get("dedup_merged_chunk_ids", []),
        "preview": r.text[:preview_chars],
        "text": r.text,
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
        "pre_expansion_chunks": [_chunk_trace(r, preview_chars) for r in pre_expansion],
        "chunks": [_chunk_trace(r, preview_chars) for r in selected],
    }


def _package(
    answer: str,
    sources: list[dict],
    abstained: bool,
    selection: SelectionResult,
    prompt: str | None,
    error: bool = False,
    debug: bool = False,
) -> dict:
    response = {
        "answer": answer,
        "sources": sources,
        "contexts": [r.text for r in selection.selected],
        "context_sources": [r.metadata.get("source_id", "") for r in selection.selected],
        "abstained": abstained,
        "error": error,
    }
    if debug:
        response["debug"] = _debug_trace(
            selection.retrieved,
            selection.pre_expansion,
            selection.selected,
            prompt,
        )
    return response


def package_greeting(state: AnswerState) -> None:
    state.response = _package(
        GREETING_MESSAGE,
        sources=[],
        abstained=False,
        selection=state.selection,
        prompt=None,
        debug=state.debug_enabled,
    )


def _policy(state: AnswerState):
    if state.policy is None:
        from app.pipeline.policy import resolve_policy

        state.policy = resolve_policy().policy
    return state.policy


def rewrite_query(state: AnswerState) -> None:
    policy = _policy(state)
    if not state.session_id or not policy.query_rewriting_enabled:
        return
    from app.conversation.query_rewriter import rewrite_query as rewrite_query_with_history
    from app.conversation.session import get_history

    history = get_history(state.session_id, settings.max_conversation_turns)
    state.effective_question = rewrite_query_with_history(state.question, history)


def classify_intent(state: AnswerState, strategy_override: str | None = None) -> None:
    policy = _policy(state)
    if strategy_override is not None:
        state.strategy_name = strategy_override
        state.strategy_knobs = (
            policy.retrieval_defaults
            if state.strategy_name == "default"
            else resolve_knobs(state.strategy_name)
        )
        state.router_skipped_reason = "strategy_override"
        return
    if not policy.router_enabled:
        return

    from app.retriever.intent_router import classify

    state.router_decision = classify(state.effective_question or state.question, model=policy.router_model)
    state.strategy_name = state.router_decision.strategy
    state.strategy_knobs = (
        policy.retrieval_defaults
        if state.strategy_name == "default"
        else resolve_knobs(state.strategy_name)
    )


def plan_retrieval(state: AnswerState) -> None:
    from dataclasses import replace

    policy = _policy(state)
    knobs = (
        policy.retrieval_defaults
        if state.strategy_name == "default"
        else resolve_knobs(state.strategy_name)
    )
    defaults = policy.retrieval_defaults
    if (
        knobs.query_decomposition_enabled != policy.query_decomposition_enabled
        or knobs.query_planner_model != defaults.query_planner_model
        or knobs.reranker_backend != defaults.reranker_backend
    ):
        knobs = replace(
            knobs,
            query_decomposition_enabled=policy.query_decomposition_enabled,
            query_planner_model=defaults.query_planner_model,
            reranker_backend=defaults.reranker_backend,
        )
    state.strategy_knobs = knobs


def prepare_legal_query_separation(state: AnswerState) -> None:
    if state.query_separation_arm == "original_only":
        return
    if state.query_separation_arm != "original_plus_rewrite":
        raise ValueError(
            f"unsupported query-separation arm {state.query_separation_arm!r}"
        )
    if state.strategy_knobs.query_decomposition_enabled:
        raise ValueError(
            "legal query separation requires query decomposition to be disabled"
        )
    if state.strategy_knobs.subquery_packaging_enabled:
        raise ValueError(
            "legal query separation requires subquery packaging to be disabled"
        )

    from app.retriever.legal_query_rewriter import rewrite_legal_query

    state.legal_rewrite_decision = rewrite_legal_query(
        state.effective_question or state.question
    )


def retrieve_context(state: AnswerState) -> None:
    from app.retriever.context_selection import accepted_legal_query

    legal_query = accepted_legal_query(state.legal_rewrite_decision)
    state.selection = STRATEGIES[state.strategy_name].execute(
        state.effective_question or state.question,
        knobs=state.strategy_knobs,
        legal_query=legal_query,
    )


def gate_evidence(state: AnswerState) -> None:
    policy = _policy(state)
    from app.pipeline.evidence import evaluate_evidence

    state.evidence = evaluate_evidence(
        state, policy, authorize_paid_calls=state.facet_audit_authorize_paid_calls
    )
    if state.evidence.verdict == "insufficient":
        state.response = _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=state.selection,
            prompt=None,
            debug=state.debug_enabled,
        )


def corrective_retrieve(state: AnswerState) -> None:
    from app.observability.context import stage_timer
    from app.pipeline.corrective import corrective_retrieve as run_corrective

    with stage_timer("corrective_retrieval", in_n=len(state.selection.selected)) as stage:
        run_corrective(state, _policy(state))
        stage["out_n"] = len(state.selection.selected)
        stage["fields"] = {"fired": state.corrective_ran}


def route_model(state: AnswerState) -> None:
    from app.pipeline.model_router import select_model

    state.model_choice = select_model(_policy(state), state)


def generate_answer(state: AnswerState) -> None:
    policy = _policy(state)
    if state.model_choice is None:
        route_model(state)
    from app.pipeline.frozen_generation import generate_frozen

    result = generate_frozen(
        question=state.effective_question or state.question,
        selected=[
            {"chunk_id": r.chunk_id, "text": r.text, "score": r.score, "metadata": dict(r.metadata)}
            for r in state.selection.selected
        ],
        model=state.model_choice.model,
        later_enacted_preference=policy.later_enacted_preference_enabled,
        selfcheck_enabled=policy.selfcheck_enabled,
        generate_fn=generate,
        build_context_fn=build_context,
    )
    state.prompt = result["user_prompt"]
    state.response = _package(
        result["answer"],
        sources=result["sources"],
        abstained=result["abstained"],
        selection=state.selection,
        prompt=result["user_prompt"],
        error=result["error"],
        debug=state.debug_enabled,
    )
