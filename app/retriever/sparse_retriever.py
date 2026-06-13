from typing import cast
from app.retriever.types import RetrievalResult
from app.indexing.bm25_store import load
from app.indexing.vector_store import NON_OPERATIVE
from app.config import settings

def sparse_retriever(query_text: str) -> list[RetrievalResult]:
    retriever = load()
    if retriever is None:
        return cast(list[RetrievalResult], [])

    k = settings.sparse_top_k
    # BM25 can't filter server-side, so over-fetch a deep candidate pool and post-filter.
    # A shallow 2x cutoff can starve operative hits when superseded chunks (e.g. historical
    # constitutions) dominate the top ranks; sparse_overfetch_k keeps enough below them.
    retriever.similarity_top_k = (
        max(settings.sparse_overfetch_k, k) if settings.retrieval_operative_only else k
    )
    nodes = retriever.retrieve(query_text)

    results = [
        RetrievalResult(
            chunk_id=n.node.node_id,
            text=n.node.get_content(),
            score=n.score if n.score is not None else 0.0,
            metadata=n.node.metadata
        )
        for n in nodes
    ]
    if settings.retrieval_operative_only:
        results = [r for r in results if r.metadata.get("status") not in NON_OPERATIVE][:k]
    return results