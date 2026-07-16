from dataclasses import dataclass

from app.config import settings
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult
from app.observability.context import capture_candidates, stage_timer


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
    *,
    legal_query: str | None = None,
) -> SelectionResult:
    subquery_packaging_enabled = (
        knobs.subquery_packaging_enabled if knobs else settings.subquery_packaging_enabled
    )
    if subquery_packaging_enabled:
        if legal_query is not None:
            raise ValueError(
                "legal query separation requires subquery packaging to be disabled"
            )
        from app.retriever.subquery_retrieval import packaged_retrieve

        with stage_timer("packaged_retrieve") as stage:
            pre_expansion = packaged_retrieve(question, knobs=knobs)
            stage["out_n"] = len(pre_expansion)
        retrieved = pre_expansion
        retrieved_trace = _snapshot_results(retrieved)
    else:
        with stage_timer("hybrid_retriever") as stage:
            if legal_query is None:
                retrieved = hybrid_retriever(question, knobs=knobs)
            else:
                retrieved = hybrid_retriever(
                    question,
                    knobs=knobs,
                    legal_query=legal_query,
                )
            stage["out_n"] = len(retrieved)
        retrieved_trace = _snapshot_results(retrieved)
        # Schema 1.1 names the exact ordered pool entering reranking. This is a
        # diagnostic clone only; capture_candidates never mutates the results.
        capture_candidates(
            "fused",
            retrieved,
            query_variant="combined",
            query_text=question,
            query_ordinal=0,
            pool_role="pre_rerank_pool",
            score_field="fused_score",
        )
        with stage_timer("rerank", in_n=len(retrieved)) as stage:
            pre_expansion = rerank(question, retrieved, knobs=knobs)
            stage["out_n"] = len(pre_expansion)

    edge_expansion_enabled = (
        knobs.edge_expansion_enabled if knobs else settings.edge_expansion_enabled
    )
    if edge_expansion_enabled:
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

    sibling_expansion_enabled = (
        knobs.sibling_expansion_enabled if knobs else settings.sibling_expansion_enabled
    )
    if sibling_expansion_enabled:
        from app.retriever.sibling_expansion import expand_siblings

        before_ids = {result.chunk_id for result in selected}
        with stage_timer("sibling_expansion", in_n=len(selected)) as stage:
            selected = expand_siblings(selected, knobs=knobs)
            additions = [result for result in selected if result.chunk_id not in before_ids]
            stage["out_n"] = len(selected)
            stage["fields"] = {
                "fired": bool(additions),
                "chunks_added": len(additions),
                "leaf_groups_added": len(
                    {
                        (
                            result.metadata.get("parent_key"),
                            result.metadata.get("unit_label"),
                        )
                        for result in additions
                    }
                ),
                "added_chars": sum(len(result.text) for result in additions),
                "added_tokens": sum(
                    int(result.metadata.get("token_estimate", 0))
                    for result in additions
                ),
            }

    capture_candidates(
        "expanded",
        selected,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        selected_ids={result.chunk_id for result in selected},
    )

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

    capture_candidates(
        "selected",
        selected,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        selected_ids={result.chunk_id for result in selected},
    )

    return SelectionResult(
        retrieved=retrieved_trace,
        pre_expansion=pre_expansion,
        selected=selected,
    )
