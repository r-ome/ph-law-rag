from app.retriever.context_builder import build_context
from app.retriever.prompts import (
    SYSTEM_PROMPT, LATER_ENACTED_RULE, ABSTAIN_MESSAGE, GREETING_MESSAGE,
    is_abstention, is_conversational, build_user_prompt
)
from app.retriever.llm_client import generate, LLMError
from app.retriever.types import RetrievalResult
from app.retriever.answerability import is_answerable
from app.retriever.context_selection import SelectionResult, select_context
from app.config import settings

def _debug_trace(
    retrieved: list[RetrievalResult],
    pre_expansion: list[RetrievalResult],
    selected: list[RetrievalResult],
    prompt: str | None
) -> dict:
    return {
        "num_retrieved": len(retrieved),
        "num_pre_expansion": len(pre_expansion),
        "num_selected": len(selected),
        "prompt_length": len(prompt) if prompt else 0,
        "pre_expansion_chunks": [
            {
                "chunk_id": r.chunk_id,
                "score": r.score,
                "source_id": r.metadata.get("source_id", ""),
                "unit_label": r.metadata.get("unit_label", ""),
                "provision_id": r.metadata.get("provision_id", ""),
                "preview": r.text[:120],
            }
            for r in pre_expansion
        ],
        "chunks": [
            {
                "chunk_id": r.chunk_id,
                "score": r.score,
                "source_id": r.metadata.get("source_id", ""),
                "unit_label": r.metadata.get("unit_label", ""),
                "provision_id": r.metadata.get("provision_id", ""),
                "expanded_from_parent": bool(r.metadata.get("expanded_from_parent")),
                "consolidated": r.metadata.get("consolidated", ""),
                "dedup_merged_chunk_ids": r.metadata.get("dedup_merged_chunk_ids", []),
                "preview": r.text[:120],
            }
            for r in selected
        ],
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


def _run_pipeline(question: str, debug_enabled: bool) -> tuple[dict, list[RetrievalResult]]:
    selection = select_context(question)

    if len(selection.pre_expansion) < settings.min_chunks_for_answer:
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=selection,
            prompt=None,
            debug=debug_enabled
        ), selection.selected

    if settings.answerability_gate_enabled and not is_answerable(question, selection.pre_expansion):
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            selection=selection,
            prompt=None,
            debug=debug_enabled
        ), selection.selected

    context_block, sources = build_context(selection.selected)

    user_prompt = build_user_prompt(question, context_block)
    system_prompt = SYSTEM_PROMPT
    if settings.later_enacted_preference_enabled:
        system_prompt = SYSTEM_PROMPT + LATER_ENACTED_RULE
    try:
        answer_text = generate(system_prompt, user_prompt)
    except LLMError as e:
        return _package(
            f"The language model could not be reached: {e}",
            sources=[],
            abstained=False,
            selection=selection,
            prompt=user_prompt,
            error=True,
            debug=debug_enabled
        ), selection.selected

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
    ), selection.selected


def answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None
    ) -> dict:
    debug_enabled = settings.debug if debug is None else debug
    effective_question = question

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
            selection=SelectionResult(retrieved=[], pre_expansion=[], selected=[]),
            prompt=None,
            debug=debug_enabled,
        )
        reranked = []
    else:
        if session_id:
            from app.conversation.session import get_history
            from app.conversation.query_rewriter import rewrite_query
            history = get_history(session_id, settings.max_conversation_turns)
            effective_question = rewrite_query(question, history)
        response, reranked = _run_pipeline(effective_question, debug_enabled)

    if session_id:
        import json
        from app.conversation.session import append_turn
        append_turn(session_id, {
            "question": question,                       # original, not rewritten
            "rewritten_question": effective_question,
            "answer": response["answer"],
            "retrieved_chunks_json": json.dumps([r.chunk_id for r in reranked]),
        })
    return response
