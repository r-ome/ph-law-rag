from dataclasses import dataclass

from app.config import settings
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.types import RetrievalResult


@dataclass
class SelectionResult:
    retrieved: list[RetrievalResult]
    pre_expansion: list[RetrievalResult]
    selected: list[RetrievalResult]


def select_context(question: str) -> SelectionResult:
    if settings.subquery_packaging_enabled:
        from app.retriever.subquery_retrieval import packaged_retrieve

        pre_expansion = packaged_retrieve(question)
        retrieved = pre_expansion
    else:
        retrieved = hybrid_retriever(question)
        pre_expansion = rerank(question, retrieved)

    if settings.edge_expansion_enabled:
        pre_expansion = expand_with_edges(question, pre_expansion)

    if settings.prefer_operative_enabled:
        from app.retriever.prefer_operative import prefer_operative

        pre_expansion = prefer_operative(pre_expansion)

    selected = pre_expansion
    if settings.parent_expansion_enabled:
        from app.retriever.parent_expansion import expand_parents

        selected = expand_parents(selected)

    if settings.consolidated_dedup_enabled:
        from app.retriever.dedup import dedup_results

        selected = dedup_results(selected)

    return SelectionResult(
        retrieved=retrieved,
        pre_expansion=pre_expansion,
        selected=selected,
    )
