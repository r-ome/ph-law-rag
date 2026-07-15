from typing import Literal

from app.observability.context import capture_candidates, stage_timer
from app.retriever.types import RetrievalResult
from app.retriever.dense_retriever import dense_retriever
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.query_planner import plan_queries
from app.observability.logger import get_logger
from app.retriever.strategy import RetrievalKnobs

logger = get_logger(__name__)

RRF_K = 60

QueryVariant = Literal["original", "legal_rewrite"]

def _fuse(
    ranked_lists: list[list[RetrievalResult]],
    *,
    score_fields: list[str] | None = None,
) -> list[RetrievalResult]:
    scores: dict[str, float] = {}
    results: dict[str, RetrievalResult] = {}
    provenance: dict[str, dict[str, float]] = {}
    
    for list_index, ranked_list in enumerate(ranked_lists):
        score_field = score_fields[list_index] if score_fields else None
        for rank, r in enumerate(ranked_list):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1 / (RRF_K + rank)
            results.setdefault(r.chunk_id, r)
            if score_field is not None:
                provenance.setdefault(r.chunk_id, {}).setdefault(score_field, float(r.score))

    ordered_ids = sorted(results, key=lambda chunk_id: scores[chunk_id], reverse=True)
    fused: list[RetrievalResult] = []
    for chunk_id in ordered_ids:
        source = results[chunk_id]
        metadata = dict(source.metadata)
        metadata["_retrieval_scores"] = {
            **dict(metadata.get("_retrieval_scores", {}) or {}),
            **provenance.get(chunk_id, {}),
            "fused_score": scores[chunk_id],
        }
        fused.append(
            RetrievalResult(
                chunk_id=source.chunk_id,
                text=source.text,
                score=scores[chunk_id],
                metadata=metadata,
            )
        )
    return fused


def retrieve_hybrid_lane(
    query_text: str,
    *,
    query_variant: QueryVariant,
    query_ordinal: int,
    knobs: RetrievalKnobs,
) -> list[RetrievalResult]:
    """Run one independent dense+sparse lane and its existing within-lane RRF."""
    with stage_timer(
        "dense_retriever",
        query_variant=query_variant,
        query_ordinal=query_ordinal,
    ) as stage:
        dense = dense_retriever(query_text, knobs=knobs)
        stage["out_n"] = len(dense)
    capture_candidates(
        "dense",
        dense,
        query_variant=query_variant,
        query_text=query_text,
        query_ordinal=query_ordinal,
        score_field="dense_score",
    )
    with stage_timer(
        "sparse_retriever",
        query_variant=query_variant,
        query_ordinal=query_ordinal,
    ) as stage:
        sparse = sparse_retriever(query_text, knobs=knobs)
        stage["out_n"] = len(sparse)
    capture_candidates(
        "sparse",
        sparse,
        query_variant=query_variant,
        query_text=query_text,
        query_ordinal=query_ordinal,
        score_field="sparse_score",
    )
    with stage_timer(
        "fusion",
        in_n=len(dense) + len(sparse),
        query_variant=query_variant,
        query_ordinal=query_ordinal,
    ) as stage:
        fused = _fuse(
            [dense, sparse],
            score_fields=["dense_score", "sparse_score"],
        )
        stage["out_n"] = len(fused)
    capture_candidates(
        "fused",
        fused,
        query_variant=query_variant,
        query_text=query_text,
        query_ordinal=query_ordinal,
        score_field="fused_score",
    )
    return fused


def fuse_query_lanes(
    original: list[RetrievalResult],
    legal_rewrite: list[RetrievalResult] | None,
) -> list[RetrievalResult]:
    """Equal-weight cross-query RRF with original-lane precedence on ties."""
    if legal_rewrite is None:
        return original

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    original_by_id = {result.chunk_id: result for result in original}
    rewrite_by_id = {result.chunk_id: result for result in legal_rewrite}
    original_ranks = {
        result.chunk_id: rank for rank, result in enumerate(original, start=1)
    }
    rewrite_ranks = {
        result.chunk_id: rank for rank, result in enumerate(legal_rewrite, start=1)
    }
    next_ordinal = 0
    for lane in (original, legal_rewrite):
        for rank, result in enumerate(lane):
            if result.chunk_id not in first_seen:
                first_seen[result.chunk_id] = next_ordinal
                next_ordinal += 1
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1 / (
                RRF_K + rank
            )

    ordered_ids = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id]),
    )
    combined: list[RetrievalResult] = []
    for chunk_id in ordered_ids:
        source = original_by_id.get(chunk_id) or rewrite_by_id[chunk_id]
        metadata = dict(source.metadata)
        provenance = dict(metadata.get("_retrieval_scores", {}) or {})
        original_result = original_by_id.get(chunk_id)
        rewrite_result = rewrite_by_id.get(chunk_id)
        if original_result is not None:
            provenance.update(
                {
                    "original_fused_score": float(original_result.score),
                    "original_lane_rank": original_ranks[chunk_id],
                }
            )
        if rewrite_result is not None:
            provenance.update(
                {
                    "legal_rewrite_fused_score": float(rewrite_result.score),
                    "legal_rewrite_lane_rank": rewrite_ranks[chunk_id],
                }
            )
        provenance["cross_query_rrf_score"] = scores[chunk_id]
        metadata["_retrieval_scores"] = provenance
        combined.append(
            RetrievalResult(
                chunk_id=source.chunk_id,
                text=source.text,
                score=scores[chunk_id],
                metadata=metadata,
            )
        )
    return combined

def hybrid_retriever(
    query_text: str,
    knobs: RetrievalKnobs | None = None,
    *,
    query_variant: str = "original",
    query_ordinal: int = 0,
    legal_query: str | None = None,
) -> list[RetrievalResult]:
    resolved_knobs = knobs or RetrievalKnobs.from_settings()
    if legal_query is not None:
        if resolved_knobs.query_decomposition_enabled:
            raise ValueError(
                "legal query separation requires query decomposition to be disabled"
            )
        original = retrieve_hybrid_lane(
            query_text,
            query_variant="original",
            query_ordinal=0,
            knobs=resolved_knobs,
        )
        rewritten = retrieve_hybrid_lane(
            legal_query,
            query_variant="legal_rewrite",
            query_ordinal=1,
            knobs=resolved_knobs,
        )
        return fuse_query_lanes(original, rewritten)

    subqueries = plan_queries(query_text, knobs=knobs)
    if len(subqueries) == 1 and subqueries[0] == query_text:
        return retrieve_hybrid_lane(
            query_text,
            query_variant=(
                "legal_rewrite" if query_variant == "legal_rewrite" else "original"
            ),
            query_ordinal=query_ordinal,
            knobs=resolved_knobs,
        )
    
    ranked_lists: list[list[RetrievalResult]] = []
    score_fields: list[str] = []
    
    for subquery_index, subquery in enumerate(subqueries):
        lane_ordinal = query_ordinal + subquery_index
        lane_variant = query_variant
        if query_variant == "original" and (len(subqueries) > 1 or subquery != query_text):
            lane_variant = "facet"
        with stage_timer(
            "dense_retriever",
            query_variant=lane_variant,
            query_ordinal=lane_ordinal,
        ) as stage:
            dense = dense_retriever(subquery, knobs=knobs)
            stage["out_n"] = len(dense)
        capture_candidates(
            "dense",
            dense,
            query_variant=lane_variant,
            query_text=subquery,
            query_ordinal=lane_ordinal,
            score_field="dense_score",
        )
        with stage_timer(
            "sparse_retriever",
            query_variant=lane_variant,
            query_ordinal=lane_ordinal,
        ) as stage:
            sparse = sparse_retriever(subquery, knobs=knobs)
            stage["out_n"] = len(sparse)
        capture_candidates(
            "sparse",
            sparse,
            query_variant=lane_variant,
            query_text=subquery,
            query_ordinal=lane_ordinal,
            score_field="sparse_score",
        )
        logger.debug(
            "hybrid_subquery_retrieved",
            subquery=subquery,
            dense_count=len(dense),
            sparse_count=len(sparse),
            dense_top_score=dense[0].score if dense else None,
            sparse_top_score=sparse[0].score if sparse else None,
        )
        ranked_lists.append(dense)
        ranked_lists.append(sparse)
        score_fields.extend(("dense_score", "sparse_score"))
        
    with stage_timer("fusion", in_n=sum(len(items) for items in ranked_lists)) as stage:
        fused = _fuse(ranked_lists, score_fields=score_fields)
        stage["out_n"] = len(fused)
    capture_candidates(
        "fused",
        fused,
        query_variant=query_variant,
        query_text=query_text,
        query_ordinal=query_ordinal,
        score_field="fused_score",
    )
    logger.debug("hybrid_fused", subqueries=len(subqueries), count=len(fused), top_score=fused[0].score if fused else None)
    return fused
