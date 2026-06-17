from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.query_planner import plan_queries

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

def hybrid_retriever (query_text: str) -> list[RetrievalResult]:
    subqueries = plan_queries(query_text)
    
    ranked_lists: list[list[RetrievalResult]] = []
    
    for subquery in subqueries:
        ranked_lists.append(dense_retriever(subquery))
        ranked_lists.append(sparse_retriever(subquery))
        
    return _fuse(ranked_lists)