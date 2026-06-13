from app.retriever.types import RetrievalResult
from app.retriever.edges import neighbors
from app.retriever.dense_retriever import dense_retriever
from app.retriever.reranker import rerank
from app.config import settings

def expand_with_edges(question: str, seed: list[RetrievalResult]) -> list[RetrievalResult]:
    if not seed:
        return seed

    seed_titles = {
        sid: r.metadata.get("title", sid)
        for r in seed if (sid := r.metadata.get("source_id"))
    }
    seed_docs = set(seed_titles)

    # neighbor -> full label e.g. "amends The Judiciary Reorganization Act of 1980"
    targets: dict[str, str] = {}
    for sid in seed_docs:
        for nbr, phrase in neighbors(sid).items():
            if nbr not in seed_docs:
                targets[nbr] = f"{phrase} {seed_titles[sid]}"
    if not targets:
        return seed

    extra: list[RetrievalResult] = []
    for nbr, label in targets.items():
        for r in dense_retriever(question, source_id=nbr, top_k=settings.edge_hop_top_k):
            r.metadata["_edge_relation"] = label
            extra.append(r)
    if not extra:
        return seed

    return rerank(question, _dedup(seed + extra))

def _dedup(results: list[RetrievalResult]) -> list[RetrievalResult]:
    seen: set[str] = set()
    out: list[RetrievalResult] = []
    for r in results:
        if r.chunk_id not in seen:
            seen.add(r.chunk_id)
            out.append(r)
    return out
    