from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.hybrid_retriever import _fuse
from app.retriever.query_planner import _plan
from app.retriever.reranker import rerank
from app.config import settings


def packaged_retrieve(question: str) -> list[RetrievalResult]:
    """Per-subquery rerank with reserved slots, capped to baseline context size.

    Atomic questions fall back to the normal full fuse+rerank. Multi-facet questions
    reserve top-N per facet, merge round-robin (rank-1 of every facet first, then
    rank-2 ...), then cap to rerank_top_n so context budget matches baseline.
    """
    subqueries = _plan(question)

    if len(subqueries) <= 1:                       # baseline path (output-identical)
        fused = _fuse([dense_retriever(question), sparse_retriever(question)])
        return rerank(question, fused)

    per_sub: list[list[RetrievalResult]] = []
    for sub in subqueries:
        fused = _fuse([dense_retriever(sub), sparse_retriever(sub)])
        per_sub.append(rerank(sub, fused)[: settings.subquery_reserve_n])

    seen: set[str] = set()
    ordered: list[RetrievalResult] = []
    for rank in range(settings.subquery_reserve_n):     # round-robin, no cross-query score sort
        for lst in per_sub:
            if rank < len(lst) and lst[rank].chunk_id not in seen:
                seen.add(lst[rank].chunk_id)
                ordered.append(lst[rank])

    return ordered[: settings.rerank_top_n]             # context-budget parity with baseline
