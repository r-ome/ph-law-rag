from dataclasses import dataclass

from app.config import settings
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult
from app.observability.context import stage_timer


@dataclass
class SelectionResult:
    retrieved: list[RetrievalResult]
    pre_expansion: list[RetrievalResult]
    selected: list[RetrievalResult]


def _snapshot_results(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        RetrievalResult(
            chunk_id=r.chunk_id,
            text=r.text,
            score=r.score,
            metadata=dict(r.metadata),
        )
        for r in results
    ]


def select_context(
    question: str,
    knobs: RetrievalKnobs | None = None,
) -> SelectionResult:
    if settings.subquery_packaging_enabled:
        from app.retriever.subquery_retrieval import packaged_retrieve

        with stage_timer("packaged_retrieve") as stage:
            pre_expansion = packaged_retrieve(question, knobs=knobs)
            stage["out_n"] = len(pre_expansion)
        retrieved = pre_expansion
        retrieved_trace = _snapshot_results(retrieved)
    else:
        with stage_timer("hybrid_retriever") as stage:
            retrieved = hybrid_retriever(question, knobs=knobs)
            stage["out_n"] = len(retrieved)
        retrieved_trace = _snapshot_results(retrieved)
        with stage_timer("rerank", in_n=len(retrieved)) as stage:
            pre_expansion = rerank(question, retrieved, knobs=knobs)
            stage["out_n"] = len(pre_expansion)

    if settings.edge_expansion_enabled:
        before = len(pre_expansion)
        with stage_timer("edge_expansion", in_n=before) as stage:
            pre_expansion = expand_with_edges(question, pre_expansion, knobs=knobs)
            stage["out_n"] = len(pre_expansion)
            stage["fields"] = {"fired": len(pre_expansion) != before}

    prefer_operative_enabled = (
        knobs.prefer_operative_enabled if knobs else settings.prefer_operative_enabled
    )
    if prefer_operative_enabled:
        from app.retriever.prefer_operative import prefer_operative

        before_ids = [r.chunk_id for r in pre_expansion]
        with stage_timer("prefer_operative", in_n=len(pre_expansion)) as stage:
            pre_expansion = prefer_operative(pre_expansion, knobs=knobs)
            stage["out_n"] = len(pre_expansion)
            stage["fields"] = {"fired": [r.chunk_id for r in pre_expansion] != before_ids}

    selected = pre_expansion
    parent_expansion_enabled = (
        knobs.parent_expansion_enabled if knobs else settings.parent_expansion_enabled
    )
    if parent_expansion_enabled:
        from app.retriever.parent_expansion import expand_parents

        before_ids = [r.chunk_id for r in selected]
        with stage_timer("parent_expansion", in_n=len(selected)) as stage:
            selected = expand_parents(selected, knobs=knobs)
            stage["out_n"] = len(selected)
            stage["fields"] = {"fired": [r.chunk_id for r in selected] != before_ids}

    consolidated_dedup_enabled = (
        knobs.consolidated_dedup_enabled if knobs else settings.consolidated_dedup_enabled
    )
    if consolidated_dedup_enabled:
        from app.retriever.dedup import dedup_results

        before = len(selected)
        with stage_timer("dedup", in_n=before) as stage:
            selected = dedup_results(selected)
            stage["out_n"] = len(selected)
            stage["fields"] = {"fired": len(selected) != before}

    return SelectionResult(
        retrieved=retrieved_trace,
        pre_expansion=pre_expansion,
        selected=selected,
    )
