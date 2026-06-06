from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.context_builder import build_context
from app.retriever.prompts import SYSTEM_PROMPT, ABSTAIN_MESSAGE, build_user_prompt
from app.retriever.llm_client import generate, LLMError
from app.retriever.types import RetrievalResult
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
) -> dict:
    response = {
        "answer": answer,
        "sources": sources,
        "abstained": abstained,
        "error": error
    }
    if settings.debug:
        response["debug"] = _debug_trace(retrieved, reranked, prompt)
    return response


def answer(question: str) -> dict:
    retrieved = hybrid_retriever(question)
    reranked = rerank(question, retrieved)
    
    if len(reranked) < settings.min_chunks_for_answer:
        return _package(
            ABSTAIN_MESSAGE,
            sources=[],
            abstained=True,
            retrieved=retrieved,
            reranked=reranked,
            prompt=None
        )
    
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
            error=True
        )
        
    soft_abstained = answer_text.strip() == ABSTAIN_MESSAGE
    
    return _package(
        answer_text,
        sources=[] if soft_abstained else sources,
        abstained=False,
        retrieved=retrieved,
        reranked=reranked,
        prompt=user_prompt
    )