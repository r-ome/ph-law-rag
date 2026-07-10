from app.retriever.types import RetrievalResult
from app.indexing.embedder import get_embed_model
from app.indexing.index_service import get_qdrant_client
from app.indexing.vector_store import query, operative_filter
from app.config import settings
from app.observability.logger import get_logger
from app.retriever.strategy import RetrievalKnobs

logger = get_logger(__name__)

def _format_embedding_query(query_text: str) -> str:
    """Qwen3-style asymmetric query prefix. No-op when no instruction is set
    (nomic path), so documents and legacy behavior are unchanged."""
    instruction = settings.embedding_query_instruction
    if instruction:
        return f"Instruct: {instruction}\nQuery: {query_text}"
    return query_text

def dense_retriever(
    query_text: str,
    source_id: str | None = None,
    top_k: int | None = None,
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    embed_model = get_embed_model()
    query_vector = embed_model.get_query_embedding(_format_embedding_query(query_text))
    
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
    max_distance = knobs.max_distance if knobs else settings.max_distance
    for p in points:
        distance = 1 - p.score
        if distance <= max_distance:
            results.append(RetrievalResult(
                chunk_id=str(p.id),
                text=p.payload["text"],
                score=p.score,
                metadata={k:v for k, v in p.payload.items() if k != "text"},
            ))
    logger.debug(
        "dense_candidates",
        query=query_text[:120],
        max_distance=max_distance,
        n_candidates=len(points),
        n_kept=len(results),
        candidate_distances=[round(1 - p.score, 4) for p in points],
        kept_distances=[round(1 - r.score, 4) for r in results],
    )
    return results
