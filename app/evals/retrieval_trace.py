"""Flatten internal candidate snapshots into crash-tolerant eval artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.evals.retrieval_targets import target_match_flags


def candidate_count_metadata(
    candidate_stages: list[dict[str, Any]],
) -> tuple[int, dict[str, int], dict[str, dict[str, int]]]:
    """Count the canonical fused pool once while retaining lane diagnostics."""
    has_canonical_pool = any(
        snapshot.get("stage") == "fused"
        and snapshot.get("pool_role") == "pre_rerank_pool"
        for snapshot in candidate_stages
    )
    candidate_count = 0
    stage_counts: dict[str, int] = {}
    variant_counts: dict[str, dict[str, int]] = {}
    for snapshot in candidate_stages:
        stage = str(snapshot.get("stage", ""))
        variant = str(snapshot.get("query_variant", "original"))
        count = len(snapshot.get("candidates", []))
        if stage == "fused" and snapshot.get("pool_role") != "pre_rerank_pool":
            variant_stage = variant_counts.setdefault("fused", {})
            variant_stage[variant] = variant_stage.get(variant, 0) + count
            if has_canonical_pool:
                continue
        elif stage in {"dense", "sparse"}:
            variant_stage = variant_counts.setdefault(stage, {})
            variant_stage[variant] = variant_stage.get(variant, 0) + count
        stage_counts[stage] = stage_counts.get(stage, 0) + count
        candidate_count += count
    return candidate_count, stage_counts, variant_counts


def candidate_lines(
    item: dict[str, Any],
    response: dict[str, Any],
    trace_record: dict[str, Any],
    target_record: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    eval_id = item.get("id", item.get("eval_id"))
    targets = (target_record or {}).get("targets", [])
    source_targets = sorted({target["source_id"] for target in targets})
    provision_targets = sorted(
        {
            f"{target['source_id']}|{target['provision_id']}"
            for target in targets
            if target.get("provision_id")
        }
    )
    leaf_targets = sorted(
        {
            f"{target['source_id']}|{target['provision_id']}|{target['unit_label']}"
            for target in targets
            if target.get("provision_id") and target.get("unit_label")
        }
    )
    lines: list[dict[str, Any]] = []
    for snapshot_index, snapshot in enumerate(
        trace_record.get("candidate_stages", []), start=1
    ):
        stage = snapshot.get("stage", "")
        for candidate in snapshot.get("candidates", []):
            metadata = candidate.get("metadata", {}) or {}
            source_id = str(metadata.get("source_id", ""))
            provision_id = str(metadata.get("provision_id", ""))
            unit_label = str(metadata.get("unit_label", ""))
            parent_id = str(
                metadata.get("parent_id")
                or metadata.get("parent_chunk_id")
                or metadata.get("provision_parent_id")
                or ""
            )
            text = str(candidate.get("text", ""))
            selected = bool(
                candidate.get("selected", stage in {"selected", "corrective"})
            )
            survived = bool(candidate.get("survived", selected))
            source_key = source_id
            provision_key = f"{source_id}|{provision_id}"
            leaf_key = f"{source_id}|{provision_id}|{unit_label}"
            lines.append(
                {
                    "record_type": "candidate",
                    "eval_id": eval_id,
                    "category": item.get("category"),
                    "split": item.get("split"),
                    "stage": stage,
                    "pool_role": snapshot.get("pool_role"),
                    "snapshot_ordinal": snapshot_index,
                    "query_variant": snapshot.get("query_variant", "original"),
                    "query_text": snapshot.get("query_text", item.get("question", "")),
                    "query_ordinal": int(snapshot.get("query_ordinal", 0)),
                    "rank": int(candidate.get("rank", 0)),
                    "chunk_id": str(candidate.get("chunk_id", "")),
                    "source_id": source_id,
                    "provision_id": provision_id,
                    "parent_id": parent_id,
                    "unit_label": unit_label,
                    "expanded_from_sibling": bool(
                        metadata.get("expanded_from_sibling")
                    ),
                    "sibling_seed_chunk_id": str(
                        metadata.get("sibling_seed_chunk_id", "")
                    ),
                    "sibling_offset": metadata.get("sibling_offset"),
                    "text": text,
                    "char_count": len(text),
                    "token_estimate": math.ceil(len(text) / 4),
                    "raw_score": candidate.get("score"),
                    "dense_score": candidate.get("dense_score"),
                    "sparse_score": candidate.get("sparse_score"),
                    "fused_score": candidate.get("fused_score"),
                    "original_fused_score": candidate.get("original_fused_score"),
                    "original_lane_rank": candidate.get("original_lane_rank"),
                    "legal_rewrite_fused_score": candidate.get(
                        "legal_rewrite_fused_score"
                    ),
                    "legal_rewrite_lane_rank": candidate.get(
                        "legal_rewrite_lane_rank"
                    ),
                    "cross_query_rrf_score": candidate.get(
                        "cross_query_rrf_score"
                    ),
                    "rerank_score": candidate.get("rerank_score"),
                    "selected": selected,
                    "survived": survived,
                    "match_mode": (target_record or {}).get("match_mode"),
                    "target_source_count": len(source_targets),
                    "target_provision_count": len(provision_targets),
                    "target_leaf_count": len(leaf_targets),
                    "matched_source_targets": (
                        [source_key] if source_key in source_targets else []
                    ),
                    "matched_provision_targets": (
                        [provision_key] if provision_key in provision_targets else []
                    ),
                    "matched_leaf_targets": (
                        [leaf_key] if leaf_key in leaf_targets else []
                    ),
                    "abstained": bool(response.get("abstained")),
                    "retrieval_latency_ms": float(
                        trace_record.get("retrieval_latency_ms", 0.0)
                    ),
                    **target_match_flags(
                        source_id=source_id,
                        provision_id=provision_id,
                        unit_label=unit_label,
                        target_record=target_record,
                    ),
                }
            )
    return lines


def append_completed_row(
    path: Path,
    eval_id: str,
    lines: list[dict[str, Any]],
    *,
    retrieval_latency_ms: float,
    abstained: bool,
    category: str | None,
    target_record: dict[str, Any] | None = None,
    candidate_count: int | None = None,
    stage_candidate_counts: dict[str, int] | None = None,
    stage_candidate_counts_by_query_variant: dict[str, dict[str, int]] | None = None,
    stage_timings_ms: dict[str, float] | None = None,
) -> None:
    """Append one row in one write, with its completion sentinel last."""
    path.parent.mkdir(parents=True, exist_ok=True)
    targets = (target_record or {}).get("targets", [])
    sentinel = {
        "record_type": "row_complete",
        "eval_id": eval_id,
        "category": category,
        "abstained": abstained,
        "candidate_count": len(lines) if candidate_count is None else candidate_count,
        "retrieval_latency_ms": round(float(retrieval_latency_ms), 2),
        "stage_candidate_counts": stage_candidate_counts or {},
        "stage_candidate_counts_by_query_variant": (
            stage_candidate_counts_by_query_variant or {}
        ),
        "stage_timings_ms": stage_timings_ms or {},
        "match_mode": (target_record or {}).get("match_mode"),
        "target_source_count": len({target["source_id"] for target in targets}),
        "target_provision_count": len(
            {
                (target["source_id"], target.get("provision_id"))
                for target in targets
                if target.get("provision_id")
            }
        ),
        "target_leaf_count": len(
            {
                (
                    target["source_id"],
                    target.get("provision_id"),
                    target.get("unit_label"),
                )
                for target in targets
                if target.get("provision_id") and target.get("unit_label")
            }
        ),
    }
    payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in [*lines, sentinel]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def read_completed_trace(path: str | Path) -> list[dict[str, Any]]:
    """Skip malformed JSON and exclude every eval ID without a sentinel."""
    path = Path(path)
    candidates: list[dict[str, Any]] = []
    completed: set[str] = set()
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            eval_id = record.get("eval_id")
            if not isinstance(eval_id, str):
                continue
            if record.get("record_type") == "row_complete":
                completed.add(eval_id)
            elif record.get("record_type") == "candidate":
                candidates.append(record)
    return [record for record in candidates if record.get("eval_id") in completed]


def completed_sentinels(path: str | Path) -> list[dict[str, Any]]:
    sentinels: list[dict[str, Any]] = []
    path = Path(path)
    if not path.exists():
        return sentinels
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(record, dict) and record.get("record_type") == "row_complete":
                sentinels.append(record)
    return sentinels
