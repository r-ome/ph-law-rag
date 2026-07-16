"""Retrieval-only metrics built from completed candidate-trace rows."""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.evals import artifacts
from app.evals.retrieval_trace import completed_sentinels, read_completed_trace

KS = (1, 3, 5, 8, 10, 30)
QUALITY_STAGES = ("dense", "sparse", "fused", "reranked", "expanded", "selected", "corrective")
TIMING_STAGES = (*QUALITY_STAGES, "sibling_expansion")
LANE_STAGES = ("dense", "sparse", "fused")
BASE_QUERY_VARIANTS = ("original", "legal_rewrite")


def _mean(values: list[float]) -> float | None:
    return round(float(statistics.mean(values)), 4) if values else None


def _ordered_unique(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        records,
        key=lambda record: (
            int(record.get("snapshot_ordinal", 0)),
            int(record.get("query_ordinal", 0)),
            int(record.get("rank", 0)),
        ),
    )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in ordered:
        chunk_id = str(record.get("chunk_id", ""))
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        unique.append(record)
    return unique


def _stage_pool(
    row_records: list[dict[str, Any]],
    stage: str,
    *,
    survivors_only: bool = False,
    before_snapshot: int | None = None,
) -> list[dict[str, Any]]:
    records = [record for record in row_records if record.get("stage") == stage]
    if before_snapshot is not None:
        records = [
            record
            for record in records
            if int(record.get("snapshot_ordinal", 0)) < before_snapshot
        ]
    if stage == "fused":
        canonical = [
            record
            for record in records
            if record.get("pool_role") == "pre_rerank_pool"
        ]
        if canonical:
            records = canonical
    if survivors_only and stage == "reranked":
        records = [record for record in records if record.get("survived")]
    return _ordered_unique(records)


def _stage_variant_pool(
    row_records: list[dict[str, Any]],
    stage: str,
    query_variant: str,
    *,
    before_snapshot: int | None = None,
) -> list[dict[str, Any]]:
    records = [
        record
        for record in row_records
        if record.get("stage") == stage
        and record.get("query_variant", "original") == query_variant
    ]
    if before_snapshot is not None:
        records = [
            record
            for record in records
            if int(record.get("snapshot_ordinal", 0)) < before_snapshot
        ]
    if stage == "fused":
        # The combined canonical clone belongs to aggregate fused metrics, not
        # to a retrieval lane.
        records = [
            record
            for record in records
            if record.get("pool_role") != "pre_rerank_pool"
        ]
    return _ordered_unique(records)


def _matched(pool: list[dict[str, Any]], field: str, k: int) -> set[str]:
    return {
        target
        for record in pool[:k]
        for target in record.get(field, [])
    }


def _first_match_rank(pool: list[dict[str, Any]], field: str) -> int | None:
    for rank, record in enumerate(pool, start=1):
        if record.get(field):
            return rank
    return None


def _stage_row_metrics(
    pool: list[dict[str, Any]],
    sentinel: dict[str, Any],
) -> dict[str, Any]:
    counts = {
        "source": int(sentinel.get("target_source_count", 0)),
        "provision": int(sentinel.get("target_provision_count", 0)),
        "leaf": int(sentinel.get("target_leaf_count", 0)),
    }
    fields = {
        "source": "matched_source_targets",
        "provision": "matched_provision_targets",
        "leaf": "matched_leaf_targets",
    }
    metrics: dict[str, Any] = {"candidate_count": len(pool)}
    for identity, field in fields.items():
        target_count = counts[identity]
        if target_count == 0:
            continue
        metrics[f"{identity}_hit_at_k"] = {
            str(k): float(bool(_matched(pool, field, k))) for k in KS
        }
        metrics[f"{identity}_recall_at_k"] = {
            str(k): len(_matched(pool, field, k)) / target_count for k in KS
        }
        if identity in {"provision", "leaf"}:
            rank = _first_match_rank(pool, field)
            metrics[f"{identity}_mrr"] = 1 / rank if rank else 0.0
    return metrics


def _aggregate_stage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "rows": len(rows),
        "candidate_count_mean": _mean(
            [float(row["metrics"]["candidate_count"]) for row in rows]
        ),
    }
    for identity in ("source", "provision", "leaf"):
        applicable = [
            row["metrics"]
            for row in rows
            if f"{identity}_hit_at_k" in row["metrics"]
        ]
        result[f"{identity}_applicable_rows"] = len(applicable)
        if not applicable:
            continue
        result[f"{identity}_hit_at_k"] = {
            str(k): _mean([m[f"{identity}_hit_at_k"][str(k)] for m in applicable])
            for k in KS
        }
        result[f"{identity}_recall_at_k"] = {
            str(k): _mean([m[f"{identity}_recall_at_k"][str(k)] for m in applicable])
            for k in KS
        }
        if identity in {"provision", "leaf"}:
            result[f"{identity}_mrr"] = _mean(
                [m[f"{identity}_mrr"] for m in applicable]
            )
    result["parent_provision_coverage"] = (
        result.get("provision_recall_at_k", {}).get("30")
    )
    result["exact_leaf_coverage"] = result.get("leaf_recall_at_k", {}).get("30")
    return result


def _target_keys(pool: list[dict[str, Any]], field: str) -> set[str]:
    return {target for record in pool for target in record.get(field, [])}


def _matches_expected_target(record: dict[str, Any]) -> bool:
    if record.get("match_mode") == "source_only":
        return bool(record.get("expected_source_match"))
    return bool(record.get("expected_provision_match"))


def _quality_summary(
    candidates: list[dict[str, Any]],
    sentinels: list[dict[str, Any]],
) -> dict[str, Any]:
    by_eval: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in candidates:
        by_eval[record["eval_id"]].append(record)
    sentinel_by_id = {record["eval_id"]: record for record in sentinels}

    stage_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_variant_rows: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    survival_values: dict[str, list[float]] = defaultdict(list)
    leaf_survival_values: dict[str, list[float]] = defaultdict(list)
    expansion_irrelevant: list[float] = []
    corrective_irrelevant: list[float] = []
    final_chars: list[float] = []
    final_tokens: list[float] = []
    sibling_fired_rows = 0
    sibling_chunks_added: list[float] = []
    sibling_chunks_added_all: list[float] = []
    sibling_target_additions = 0
    sibling_total_additions = 0
    leaf_rows_missed_after_rerank = 0
    leaf_rows_recovered_expanded = 0
    leaf_rows_recovered_selected = 0

    for eval_id, sentinel in sentinel_by_id.items():
        row_records = by_eval.get(eval_id, [])
        boundary = min(
            (
                int(record.get("snapshot_ordinal", 0))
                for record in row_records
                if record.get("stage") in {"expanded", "selected"}
            ),
            default=None,
        )
        pools = {
            stage: _stage_pool(
                row_records,
                stage,
                before_snapshot=(
                    boundary if stage in {"dense", "sparse", "fused", "reranked"} else None
                ),
            )
            for stage in QUALITY_STAGES
        }
        traced_stages = set((sentinel.get("stage_candidate_counts") or {}).keys())
        for stage, pool in pools.items():
            if pool or stage in traced_stages:
                stage_rows[stage].append(
                    {"eval_id": eval_id, "metrics": _stage_row_metrics(pool, sentinel)}
                )
        traced_variants = sentinel.get("stage_candidate_counts_by_query_variant") or {}
        discovered_variants = {
            str(record.get("query_variant", "original"))
            for record in row_records
            if record.get("stage") in LANE_STAGES
            and record.get("pool_role") != "pre_rerank_pool"
        }
        for stage in LANE_STAGES:
            variants = set(BASE_QUERY_VARIANTS) | discovered_variants | set(
                (traced_variants.get(stage) or {}).keys()
            )
            for variant in variants:
                pool = _stage_variant_pool(
                    row_records,
                    stage,
                    variant,
                    before_snapshot=boundary,
                )
                if pool or variant in (traced_variants.get(stage) or {}):
                    stage_variant_rows[stage][variant].append(
                        {
                            "eval_id": eval_id,
                            "metrics": _stage_row_metrics(pool, sentinel),
                        }
                    )

        union = _ordered_unique([*pools["dense"], *pools["sparse"]])
        target_field = (
            "matched_provision_targets"
            if int(sentinel.get("target_provision_count", 0))
            else "matched_source_targets"
        )
        union_targets = _target_keys(union, target_field)
        leaf_union_targets = _target_keys(union, "matched_leaf_targets")
        survival_pools = {
            "union": union,
            "fused": pools["fused"],
            "reranked": _stage_pool(
                row_records,
                "reranked",
                survivors_only=True,
                before_snapshot=boundary,
            ),
            "expanded": pools["expanded"],
            "selected": pools["selected"],
            "corrective": pools["corrective"],
        }
        if union_targets:
            for stage, pool in survival_pools.items():
                if stage == "corrective" and not pool:
                    continue
                survival_values[stage].append(
                    len(_target_keys(pool, target_field) & union_targets) / len(union_targets)
                )
        if leaf_union_targets:
            for stage, pool in survival_pools.items():
                if stage == "corrective" and not pool:
                    continue
                leaf_survival_values[stage].append(
                    len(_target_keys(pool, "matched_leaf_targets") & leaf_union_targets)
                    / len(leaf_union_targets)
                )

        reranked_survivors = _stage_pool(
            row_records,
            "reranked",
            survivors_only=True,
            before_snapshot=boundary,
        )
        reranked_ids = {record["chunk_id"] for record in reranked_survivors}
        sibling_additions = [
            record
            for record in pools["expanded"]
            if record.get("expanded_from_sibling")
        ]
        sibling_chunks_added_all.append(float(len(sibling_additions)))
        if sibling_additions:
            sibling_fired_rows += 1
            sibling_chunks_added.append(float(len(sibling_additions)))
            sibling_total_additions += len(sibling_additions)
            sibling_target_additions += sum(
                bool(record.get("expected_leaf_match"))
                for record in sibling_additions
            )
        if int(sentinel.get("target_leaf_count", 0)):
            reranked_leaf_targets = _target_keys(
                reranked_survivors, "matched_leaf_targets"
            )
            if not reranked_leaf_targets:
                leaf_rows_missed_after_rerank += 1
                if _target_keys(sibling_additions, "matched_leaf_targets"):
                    leaf_rows_recovered_expanded += 1
                    if _target_keys(
                        pools["selected"], "matched_leaf_targets"
                    ):
                        leaf_rows_recovered_selected += 1
        expansion_irrelevant.append(
            float(
                sum(
                    record["chunk_id"] not in reranked_ids
                    and not _matches_expected_target(record)
                    for record in pools["expanded"]
                )
            )
        )
        if pools["corrective"]:
            selected_ids = {record["chunk_id"] for record in pools["selected"]}
            corrective_irrelevant.append(
                float(
                    sum(
                        record["chunk_id"] not in selected_ids
                        and not _matches_expected_target(record)
                        for record in pools["corrective"]
                    )
                )
            )

        final_pool = pools["corrective"] or pools["selected"]
        final_chars.append(float(sum(len(record.get("text", "")) for record in final_pool)))
        final_tokens.append(
            float(sum(int(record.get("token_estimate", 0)) for record in final_pool))
        )

    return {
        "rows": len(sentinels),
        "target_rows": sum(bool(row.get("target_source_count")) for row in sentinels),
        "stages": {
            stage: _aggregate_stage(rows) for stage, rows in stage_rows.items()
        },
        "stages_by_query_variant": {
            stage: {
                variant: _aggregate_stage(stage_variant_rows[stage].get(variant, []))
                for variant in (
                    *BASE_QUERY_VARIANTS,
                    *sorted(
                        set(stage_variant_rows[stage]) - set(BASE_QUERY_VARIANTS)
                    ),
                )
            }
            for stage in LANE_STAGES
        },
        "target_survival": {
            stage: _mean(values) for stage, values in survival_values.items()
        },
        "leaf_survival": {
            stage: _mean(values) for stage, values in leaf_survival_values.items()
        },
        "irrelevant_additions": {
            "expansion_mean": _mean(expansion_irrelevant),
            "corrective_mean": _mean(corrective_irrelevant),
        },
        "final_context": {
            "characters_mean": _mean(final_chars),
            "token_estimate_mean": _mean(final_tokens),
        },
        "sibling_expansion": {
            "rows_fired": sibling_fired_rows,
            "chunks_added_total": sibling_total_additions,
            "chunks_added_mean": _mean(sibling_chunks_added_all),
            "chunks_added_mean_when_fired": _mean(sibling_chunks_added),
            "target_bearing_additions": sibling_target_additions,
            "target_bearing_addition_ratio": (
                round(sibling_target_additions / sibling_total_additions, 4)
                if sibling_total_additions
                else None
            ),
            "leaf_rows_missed_after_rerank": leaf_rows_missed_after_rerank,
            "leaf_rows_recovered_at_expanded": leaf_rows_recovered_expanded,
            "leaf_rows_recovered_at_selected": leaf_rows_recovered_selected,
            "missed_leaf_recovery_rate": (
                round(
                    leaf_rows_recovered_expanded / leaf_rows_missed_after_rerank,
                    4,
                )
                if leaf_rows_missed_after_rerank
                else None
            ),
        },
        "retrieval_latency_ms_mean": _mean(
            [float(row.get("retrieval_latency_ms", 0.0)) for row in sentinels]
        ),
        "stage_latency_ms_mean": {
            stage: _mean(
                [
                    float(row.get("stage_timings_ms", {}).get(stage, 0.0))
                    for row in sentinels
                    if stage in row.get("stage_timings_ms", {})
                ]
            )
            for stage in TIMING_STAGES
            if any(stage in row.get("stage_timings_ms", {}) for row in sentinels)
        },
    }


def build_retrieval_summary(
    trace_path: str | Path,
    *,
    holdout: bool = False,
) -> dict[str, Any]:
    candidates = read_completed_trace(trace_path)
    sentinels = completed_sentinels(trace_path)
    operational_stage_latency = {
        stage: _mean(
            [
                float(row.get("stage_timings_ms", {}).get(stage, 0.0))
                for row in sentinels
                if stage in row.get("stage_timings_ms", {})
            ]
        )
        for stage in TIMING_STAGES
        if any(stage in row.get("stage_timings_ms", {}) for row in sentinels)
    }
    operational = {
        "rows": len(sentinels),
        "candidate_count_mean": _mean(
            [float(row.get("candidate_count", 0)) for row in sentinels]
        ),
        "retrieval_latency_ms_mean": _mean(
            [float(row.get("retrieval_latency_ms", 0.0)) for row in sentinels]
        ),
    }
    stage_count_names = sorted(
        {
            stage
            for row in sentinels
            for stage in (row.get("stage_candidate_counts") or {})
        }
    )
    if stage_count_names:
        operational["stage_candidate_count_mean"] = {
            stage: _mean(
                [
                    float((row.get("stage_candidate_counts") or {}).get(stage, 0))
                    for row in sentinels
                ]
            )
            for stage in stage_count_names
        }
    variant_count_stages = sorted(
        {
            stage
            for row in sentinels
            for stage in (
                row.get("stage_candidate_counts_by_query_variant") or {}
            )
        }
    )
    if variant_count_stages:
        operational["stage_candidate_count_by_query_variant_mean"] = {
            stage: {
                variant: _mean(
                    [
                        float(
                            (
                                (
                                    row.get(
                                        "stage_candidate_counts_by_query_variant"
                                    )
                                    or {}
                                ).get(stage)
                                or {}
                            ).get(variant, 0)
                        )
                        for row in sentinels
                    ]
                )
                for variant in (
                    *BASE_QUERY_VARIANTS,
                    *sorted(
                        {
                            variant
                            for row in sentinels
                            for variant in (
                                (
                                    (
                                        row.get(
                                            "stage_candidate_counts_by_query_variant"
                                        )
                                        or {}
                                    ).get(stage)
                                    or {}
                                )
                            )
                        }
                        - set(BASE_QUERY_VARIANTS)
                    ),
                )
            }
            for stage in variant_count_stages
        }
    if operational_stage_latency:
        operational["stage_latency_ms_mean"] = operational_stage_latency
    if holdout:
        return {"available": True, "holdout": True, "operational": operational}

    overall = _quality_summary(candidates, sentinels)
    categories = sorted(
        {str(row.get("category")) for row in sentinels if row.get("category")}
    )
    by_category: dict[str, Any] = {}
    for category in categories:
        category_sentinels = [
            row for row in sentinels if row.get("category") == category
        ]
        eval_ids = {row["eval_id"] for row in category_sentinels}
        by_category[category] = _quality_summary(
            [row for row in candidates if row.get("eval_id") in eval_ids],
            category_sentinels,
        )
    return {
        "available": True,
        "holdout": False,
        "operational": operational,
        "overall": overall,
        "by_category": by_category,
    }


def save_retrieval_summary(
    trace_path: str | Path,
    summary_path: str | Path,
    *,
    holdout: bool = False,
) -> dict[str, Any]:
    summary = build_retrieval_summary(trace_path, holdout=holdout)
    artifacts.write_json(Path(summary_path), summary)
    return summary


def rebuild_for_tag(tag: str) -> dict[str, Any] | None:
    paths = artifacts.paths_for_tag(tag)
    if not paths.retrieval_trace.exists():
        return None
    meta = artifacts.load_meta(tag) or {}
    return save_retrieval_summary(
        paths.retrieval_trace,
        paths.retrieval_summary,
        holdout=bool(meta.get("holdout")),
    )
