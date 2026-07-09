from typing import cast
from app.retriever.types import RetrievalResult
from app.indexing.bm25_store import load
from app.config import settings
from app.retriever.strategy import RetrievalKnobs

def sparse_retriever(
    query_text: str,
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    retriever = load()
    if retriever is None:
        return cast(list[RetrievalResult], [])

    k = knobs.sparse_top_k if knobs else settings.sparse_top_k
    retrieval_operative_only = (
        knobs.retrieval_operative_only if knobs else settings.retrieval_operative_only
    )
    sparse_overfetch_k = knobs.sparse_overfetch_k if knobs else settings.sparse_overfetch_k
    # BM25 can't filter server-side, so over-fetch a deep candidate pool and post-filter.
    # A shallow 2x cutoff can starve operative hits when superseded chunks (e.g. historical
    # constitutions) dominate the top ranks; sparse_overfetch_k keeps enough below them.
    retriever.similarity_top_k = (
        max(sparse_overfetch_k, k) if retrieval_operative_only else k
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
    if retrieval_operative_only:
        # mirror the dense filter: drop only chunks explicitly marked hide (fail-open).
        results = [r for r in results if r.metadata.get("operability_action") != "hide"][:k]
    return results
