from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever

RRF_K = 60

def hybrid_retriever (query_text: str) -> list[RetrievalResult]:
    dense = dense_retriever(query_text)
    sparse = sparse_retriever(query_text)
    
    scores: dict[str, float] = {}
    results: dict[str, RetrievalResult] = {}
    
    for ranked_list in (dense, sparse):
        for rank, r in enumerate(ranked_list):
            scores[r.chunk_id] = scores.get(r.chunk_id,0.0) + 1 /(RRF_K + rank)
            results.setdefault(r.chunk_id, r)
            
    fused = sorted(results.values(), key=lambda r: scores[r.chunk_id], reverse=True)
    for r in fused:
        r.score = scores[r.chunk_id]
    return fused