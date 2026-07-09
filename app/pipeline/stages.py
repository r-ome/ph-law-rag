import re

from app.config import settings
from app.observability.logger import get_logger
from app.retriever.answerability import is_answerable
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
    policy = _policy(state)
    state.strategy_knobs = (
        policy.retrieval_defaults
        if state.strategy_name == "default"
        else resolve_knobs(state.strategy_name)
    )


def retrieve_context(state: AnswerState) -> None:
    state.selection = STRATEGIES[state.strategy_name].execute(
        state.effective_question or state.question,
        knobs=state.strategy_knobs,
    )


def gate_evidence(state: AnswerState) -> None:
    policy = _policy(state)
    question = state.effective_question or state.question
    if policy.evidence_gate == "crag":
        raise NotImplementedError(
            "RAGLAB_PROFILE=crag-experimental is registered, but the CRAG evidence gate "
            "is not implemented until PR5."
        )
    if len(state.selection.pre_expansion) < policy.min_chunks_for_answer:
        state.response = _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=state.selection,
            prompt=None,
            debug=state.debug_enabled,
        )
        return

    if policy.evidence_gate == "answerability" and not is_answerable(question, state.selection.pre_expansion):
        state.response = _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=state.selection,
            prompt=None,
            debug=state.debug_enabled,
        )


def route_model(state: AnswerState) -> None:
    from app.pipeline.model_router import select_model

    state.model_choice = select_model(_policy(state), state)


def generate_answer(state: AnswerState) -> None:
    policy = _policy(state)
    if state.model_choice is None:
        route_model(state)
    model = state.model_choice.model
    question = state.effective_question or state.question
    context_block, sources = build_context(state.selection.selected)

    user_prompt = build_user_prompt(question, context_block)
    state.prompt = user_prompt
    system_prompt = SYSTEM_PROMPT
    if policy.later_enacted_preference_enabled:
        system_prompt = SYSTEM_PROMPT + LATER_ENACTED_RULE
    try:
        answer_text = generate(system_prompt, user_prompt, model=model)
    except LLMError as e:
        logger.warning("generation_failed", error=str(e), model=model)
        state.response = _package(
            f"The language model could not be reached: {e}",
            sources=[],
            abstained=False,
            selection=state.selection,
            prompt=user_prompt,
            error=True,
            debug=state.debug_enabled,
        )
        return

    if policy.selfcheck_enabled and not is_abstention(answer_text):
        from app.retriever.prompts import SELFCHECK_SYSTEM, build_selfcheck_prompt

        try:
            revised = generate(
                SELFCHECK_SYSTEM,
                build_selfcheck_prompt(question, context_block, answer_text),
                model=model,
            )
            if revised.strip():
                answer_text = revised
        except LLMError:
            pass

    soft_abstained = is_abstention(answer_text)
    state.response = _package(
        answer_text,
        sources=[] if soft_abstained else _cited_sources(answer_text, sources),
        abstained=soft_abstained,
        selection=state.selection,
        prompt=user_prompt,
        debug=state.debug_enabled,
    )
