from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.context_builder import build_context
from app.retriever.prompts import (
    SYSTEM_PROMPT, ABSTAIN_MESSAGE, GREETING_MESSAGE,
    is_abstention, is_conversational, build_user_prompt
)
from app.retriever.llm_client import generate, LLMError
from app.retriever.types import RetrievalResult
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.answerability import is_answerable
from app.config import settings

def _debug_trace(
    retrieved: list[RetrievalResult],
    reranked: list[RetrievalResult],
    prompt: str | None
) -> dict:
    return {
        "num_retrieved": len(retrieved),
        "num_reranked": len(reranked),
        "prompt_length": len(prompt) if prompt else 0,
        "chunks": [
            {
                "chunk_id": r.chunk_id,
                "score": r.score,
                "source_id": r.metadata.get("source_id", ""),
                "preview": r.text[:120],
            }
            for r in reranked
        ]
    }

def _package(
    answer: str,
    sources: list[dict],
    abstained: bool,
    retrieved: list[RetrievalResult],
    reranked: list[RetrievalResult],
    prompt: str | None,
    error: bool = False,
    debug: bool = False
) -> dict:
    response = {
        "answer": answer,
        "sources": sources,
        "contexts": [r.text for r in reranked],
        "context_sources": [r.metadata.get("source_id", "") for r in reranked],
        "abstained": abstained,
        "error": error
    }
    if debug:
        response["debug"] = _debug_trace(retrieved, reranked, prompt)
    return response


def _run_pipeline(question: str, debug_enabled: bool) -> tuple[dict, list[RetrievalResult]]:
    if settings.subquery_packaging_enabled:
        from app.retriever.subquery_retrieval import packaged_retrieve
        reranked = packaged_retrieve(question)
        retrieved = reranked  # no separate candidate list in this path (debug trace)
    else:
        retrieved = hybrid_retriever(question)
        reranked = rerank(question, retrieved)
    if settings.edge_expansion_enabled:
        reranked = expand_with_edges(question, reranked)

    if settings.prefer_operative_enabled:
        from app.retriever.prefer_operative import prefer_operative
        reranked = prefer_operative(reranked)

    if len(reranked) < settings.min_chunks_for_answer:
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            retrieved=retrieved,
            reranked=reranked,
            prompt=None,
            debug=debug_enabled
        ), reranked

    if settings.answerability_gate_enabled and not is_answerable(question, reranked):
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            retrieved=retrieved,
            reranked=reranked,
            prompt=None,
            debug=debug_enabled
        ), reranked

    if settings.parent_expansion_enabled:
        from app.retriever.parent_expansion import expand_parents
        reranked = expand_parents(reranked)

    context_block, sources = build_context(reranked,)

    user_prompt = build_user_prompt(question, context_block)
    try:
        answer_text = generate(SYSTEM_PROMPT, user_prompt)
    except LLMError as e:
        return _package(
            f"The language model could not be reached: {e}",
            sources=[],
            abstained=False,
            retrieved=retrieved,
            reranked=reranked,
            prompt=user_prompt,
            error=True,
            debug=debug_enabled
        ), reranked

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
        retrieved=retrieved,
        reranked=reranked,
        prompt=user_prompt,
        debug=debug_enabled
    ), reranked


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
            retrieved=[],
            reranked=[],
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
