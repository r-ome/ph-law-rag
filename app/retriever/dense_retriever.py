from app.retriever.types import RetrievalResult
from app.indexing.embedder import get_embed_model
from app.indexing.index_service import get_qdrant_client
from app.indexing.vector_store import query, operative_filter
from app.config import settings
from app.retriever.strategy import RetrievalKnobs

def dense_retriever(
    query_text: str,
    source_id: str | None = None,
    top_k: int | None = None,
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    embed_model = get_embed_model()
    query_vector = embed_model.get_query_embedding(query_text)
    
    client = get_qdrant_client()
    k = top_k if top_k is not None else (knobs.dense_top_k if knobs else settings.dense_top_k)
    points = query(
        client,
        query_vector,
        k,
        query_filter=operative_filter(
            source_id,
            retrieval_operative_only=knobs.retrieval_operative_only if knobs else None,
        ),
    )
    results = []
    for p in points:
        distance = 1 - p.score
        if distance <= settings.max_distance:
            results.append(RetrievalResult(
                chunk_id=str(p.id),
                text=p.payload["text"],
                score=p.score,
                metadata={k:v for k, v in p.payload.items() if k != "text"},
            ))
    return results
