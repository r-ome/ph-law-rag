from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.hybrid_retriever import _fuse
from app.retriever.query_planner import _plan
from app.retriever.reranker import rerank
from app.config import settings
from app.observability.context import capture_candidates, stage_timer
from app.retriever.strategy import RetrievalKnobs


def _retrieve_lane(
    query: str,
    *,
    knobs: RetrievalKnobs | None,
    query_variant: str,
    query_ordinal: int,
) -> list[RetrievalResult]:
    with stage_timer(
        "dense_retriever",
        query_variant=query_variant,
        query_ordinal=query_ordinal,
    ) as stage:
        dense = dense_retriever(query, knobs=knobs)
        stage["out_n"] = len(dense)
    capture_candidates(
        "dense",
        dense,
        query_variant=query_variant,
        query_text=query,
        query_ordinal=query_ordinal,
        score_field="dense_score",
    )
    with stage_timer(
        "sparse_retriever",
        query_variant=query_variant,
        query_ordinal=query_ordinal,
    ) as stage:
        sparse = sparse_retriever(query, knobs=knobs)
        stage["out_n"] = len(sparse)
    capture_candidates(
        "sparse",
        sparse,
        query_variant=query_variant,
        query_text=query,
        query_ordinal=query_ordinal,
        score_field="sparse_score",
    )
    with stage_timer("fusion", in_n=len(dense) + len(sparse)) as stage:
        fused = _fuse(
            [dense, sparse],
            score_fields=["dense_score", "sparse_score"],
        )
        stage["out_n"] = len(fused)
    capture_candidates(
        "fused",
        fused,
        query_variant=query_variant,
        query_text=query,
        query_ordinal=query_ordinal,
        score_field="fused_score",
    )
    return fused


def round_robin_merge(
    per_query_lists: list[list[RetrievalResult]],
    *,
    seen_ids: set[str] | None = None,
    cap: int,
) -> list[RetrievalResult]:
    seen = set(seen_ids or set())
    ordered: list[RetrievalResult] = []
    max_len = max((len(results) for results in per_query_lists), default=0)

    for rank in range(max_len):
        for results in per_query_lists:
            if len(ordered) >= cap:
                return ordered
            if rank < len(results) and results[rank].chunk_id not in seen:
                seen.add(results[rank].chunk_id)
                ordered.append(results[rank])

    return ordered


def packaged_retrieve(
    question: str,
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    """Per-subquery rerank with reserved slots, capped to baseline context size.

    Atomic questions fall back to the normal full fuse+rerank. Multi-facet questions
    reserve top-N per facet, merge round-robin (rank-1 of every facet first, then
    rank-2 ...), then cap to rerank_top_n so context budget matches baseline.
    """
    subqueries = _plan(
        question,
        model=knobs.query_planner_model if knobs else None,
        max_subqueries=knobs.query_planner_max_subqueries if knobs else None,
    )
    top_n = knobs.rerank_top_n if knobs else settings.rerank_top_n
    reserve_n = knobs.subquery_reserve_n if knobs else settings.subquery_reserve_n

    if len(subqueries) <= 1:                       # baseline path (output-identical)
        fused = _retrieve_lane(
            question,
            knobs=knobs,
            query_variant="original",
            query_ordinal=0,
        )
        return rerank(
            question,
            fused,
            knobs=knobs,
            query_variant="original",
            query_ordinal=0,
        )

    per_sub: list[list[RetrievalResult]] = []
    for ordinal, sub in enumerate(subqueries, start=1):
        fused = _retrieve_lane(
            sub,
            knobs=knobs,
            query_variant="facet",
            query_ordinal=ordinal,
        )
        per_sub.append(
            rerank(
                sub,
                fused,
                knobs=knobs,
                query_variant="facet",
                query_ordinal=ordinal,
            )[:reserve_n]
        )

    return round_robin_merge(
        per_sub,
        seen_ids=set(),
        cap=top_n,
    )                                  # context-budget parity with baseline
