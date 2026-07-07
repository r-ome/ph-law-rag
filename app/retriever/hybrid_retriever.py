from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.query_planner import plan_queries
from app.observability.logger import get_logger
from app.retriever.strategy import RetrievalKnobs

logger = get_logger(__name__)

RRF_K = 60

def _fuse(ranked_lists: list[list[RetrievalResult]]) -> list[RetrievalResult]:
    scores: dict[str, float] = {}
    results: dict[str, RetrievalResult] = {}
    
    for ranked_list in ranked_lists:
        for rank, r in enumerate(ranked_list):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1 / (RRF_K + rank)
            results.setdefault(r.chunk_id, r)
    fused = sorted(results.values(), key=lambda r: scores[r.chunk_id], reverse=True)
    for retrieved in fused:
        retrieved.score = scores[retrieved.chunk_id]
    return fused

def hybrid_retriever(
    query_text: str,
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    subqueries = plan_queries(query_text)
    
    ranked_lists: list[list[RetrievalResult]] = []
    
    for subquery in subqueries:
        dense = dense_retriever(subquery, knobs=knobs)
        sparse = sparse_retriever(subquery, knobs=knobs)
        logger.debug(
            "hybrid_subquery_retrieved",
            subquery=subquery,
            dense_count=len(dense),
            sparse_count=len(sparse),
            dense_top_score=dense[0].score if dense else None,
            sparse_top_score=sparse[0].score if sparse else None,
        )
        ranked_lists.append(dense)
        ranked_lists.append(sparse)
        
    fused = _fuse(ranked_lists)
    logger.debug("hybrid_fused", subqueries=len(subqueries), count=len(fused), top_score=fused[0].score if fused else None)
    return fused
