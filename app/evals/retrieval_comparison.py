"""Compare two sealed retrieval bundles without importing the retrieval stack."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.evals.integrity import (
    adapt_retrieval_config,
    atomic_write_json,
    file_sha256,
    paths_for,
    schema_version,
    validate_schema,
    validate_sealed_bundle,
)

_CUTOFF_KEYS = (
    "dense_top_k",
    "sparse_top_k",
    "sparse_overfetch_k",
    "rerank_top_n",
    "rerank_score_margin",
    "max_distance",
)
_SELECTION_KEYS = (
    "edge_expansion_enabled",
    "edge_hop_top_k",
    "parent_expansion_enabled",
    "parent_expansion_min_children",
    "parent_expansion_max_chars",
    "sibling_expansion_enabled",
    "sibling_expansion_radius",
    "sibling_expansion_max_chars",
    "sibling_expansion_max_tokens",
    "prefer_operative_enabled",
    "retrieval_operative_only",
    "consolidated_dedup_enabled",
    "query_decomposition_enabled",
    "query_planner_model",
    "query_planner_max_subqueries",
    "subquery_packaging_enabled",
    "subquery_reserve_n",
)


def _without_arm(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "arm"}


def _subset(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value.get(key) for key in keys}


def _identity_parts(
    meta: dict[str, Any],
    retrieval_config: dict[str, Any],
) -> dict[str, Any]:
    shared = retrieval_config["shared_values"]
    defaults = shared.get("retrieval_defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("retrieval_defaults identity is invalid")
    return {
        "dataset": meta.get("dataset_identity"),
        "targets": meta.get("targets_identity"),
        "corpus": (meta.get("corpus_identity") or {}).get("hash"),
        "index": (meta.get("index_identity") or {}).get("hash"),
        "embeddings": {
            key: shared.get(key)
            for key in (
                "embedding_backend",
                "embedding_model",
                "embedding_dim",
                "embedding_query_instruction",
                "qdrant_collection",
                "chunk_size",
                "chunk_overlap",
            )
        },
        "reranker": {
            key: shared.get(key)
            for key in (
                "reranker_backend",
                "reranker_model",
                "qwen3_reranker_model",
                "bedrock_rerank_model",
            )
        },
        "cutoffs": _subset(defaults, _CUTOFF_KEYS),
        "selection": _subset(defaults, _SELECTION_KEYS),
        "evidence": {
            key: shared.get(key)
            for key in (
                "evidence_gate",
                "evidence_judge_model",
                "min_chunks_for_answer",
                "corrective_retrieval_enabled",
            )
        },
    }


def _holdout_metadata(
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> bool:
    return (
        bool(meta.get("holdout"))
        or "holdout" in (meta.get("splits") or [])
        or any(row.get("split") == "holdout" for row in rows)
    )


def _validated_source(tag: str) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    rows, meta = validate_sealed_bundle(paths_for(tag))
    minor = validate_schema(meta.get("schema"))
    config = adapt_retrieval_config(
        meta.get("retrieval_config"),
        schema_minor=minor,
    )
    return rows, meta, config


def compare_retrieval_bundles(
    baseline_tag: str,
    candidate_tag: str,
    *,
    tag: str,
) -> Path:
    """Validate, compare, and durably publish a per-row retrieval report."""
    # Validate both sources completely before considering any output write.
    baseline_rows, baseline_meta, baseline_config = _validated_source(baseline_tag)
    candidate_rows, candidate_meta, candidate_config = _validated_source(candidate_tag)

    if _holdout_metadata(baseline_meta, baseline_rows) or _holdout_metadata(
        candidate_meta, candidate_rows
    ):
        raise ValueError("holdout retrieval bundles cannot be compared")

    baseline_arm = baseline_config["query_separation"].get("arm")
    candidate_arm = candidate_config["query_separation"].get("arm")
    if baseline_arm != "original_only":
        raise ValueError("retrieval comparison baseline arm must be original_only")
    if candidate_arm != "original_plus_rewrite":
        raise ValueError(
            "retrieval comparison candidate arm must be original_plus_rewrite"
        )
    if baseline_config["shared_hash"] != candidate_config["shared_hash"]:
        raise ValueError("retrieval comparison shared_hash mismatch")
    if _without_arm(baseline_config["query_separation"]) != _without_arm(
        candidate_config["query_separation"]
    ):
        raise ValueError("retrieval comparison query-separation config mismatch")

    baseline_parts = _identity_parts(baseline_meta, baseline_config)
    candidate_parts = _identity_parts(candidate_meta, candidate_config)
    identity_checks = {
        name: {
            "matched": baseline_parts[name] == candidate_parts[name],
            "baseline": baseline_parts[name],
            "candidate": candidate_parts[name],
        }
        for name in baseline_parts
    }
    mismatches = [name for name, check in identity_checks.items() if not check["matched"]]
    if mismatches:
        raise ValueError(
            "retrieval comparison identity mismatch: " + ", ".join(mismatches)
        )

    baseline_by_id = {row["eval_id"]: row for row in baseline_rows}
    candidate_by_id = {row["eval_id"]: row for row in candidate_rows}
    baseline_order = [row["eval_id"] for row in baseline_rows]
    if baseline_order != [row["eval_id"] for row in candidate_rows]:
        raise ValueError("retrieval comparison eval order mismatch")

    row_changes = []
    for eval_id in baseline_order:
        baseline = baseline_by_id[eval_id]
        candidate = candidate_by_id[eval_id]
        pool_changed = (
            baseline.get("pre_rerank_pool_hash")
            != candidate.get("pre_rerank_pool_hash")
        )
        context_changed = (
            baseline.get("selected_context_hash")
            != candidate.get("selected_context_hash")
        )
        row_changes.append(
            {
                "eval_id": eval_id,
                "pre_rerank_pool_changed": pool_changed,
                "selected_context_changed": context_changed,
                "baseline_pre_rerank_pool_hash": baseline.get(
                    "pre_rerank_pool_hash"
                ),
                "candidate_pre_rerank_pool_hash": candidate.get(
                    "pre_rerank_pool_hash"
                ),
                "baseline_selected_context_hash": baseline.get(
                    "selected_context_hash"
                ),
                "candidate_selected_context_hash": candidate.get(
                    "selected_context_hash"
                ),
            }
        )

    started = datetime.now().astimezone()
    report = {
        "schema": schema_version(),
        "artifact_type": "retrieval_comparison",
        "tag": tag,
        "baseline_tag": baseline_tag,
        "candidate_tag": candidate_tag,
        "baseline_arm": baseline_arm,
        "candidate_arm": candidate_arm,
        "shared_hash": baseline_config["shared_hash"],
        "query_separation_config": _without_arm(
            baseline_config["query_separation"]
        ),
        "identity_checks": identity_checks,
        "summary": {
            "row_count": len(row_changes),
            "pre_rerank_pool_changed": sum(
                row["pre_rerank_pool_changed"] for row in row_changes
            ),
            "selected_context_changed": sum(
                row["selected_context_changed"] for row in row_changes
            ),
        },
        "rows": row_changes,
    }

    # Publish into the dated run layout only after every rejection gate above.
    desired_root = paths_for(tag, started, create=False).root
    if desired_root.exists():
        raise FileExistsError(f"retrieval comparison tag already exists: {tag}")
    partial_root = desired_root.with_name(
        f".{desired_root.name}.{uuid4().hex}.partial"
    )
    report_path = partial_root / "retrieval_comparison.json"
    meta_path = partial_root / "meta.json"
    try:
        atomic_write_json(report_path, report)
        meta = {
            "schema": schema_version(),
            "artifact_type": "retrieval_comparison",
            "tag": tag,
            "date": started.strftime("%Y-%m-%d"),
            "started_at": started.isoformat(),
            "completed_at": datetime.now().astimezone().isoformat(),
            "holdout": False,
            "baseline_tag": baseline_tag,
            "candidate_tag": candidate_tag,
            "baseline_bundle_file_hash": baseline_meta["bundle_file_hash"],
            "candidate_bundle_file_hash": candidate_meta["bundle_file_hash"],
            "report_hash": file_sha256(report_path),
            "row_count": len(row_changes),
        }
        atomic_write_json(meta_path, meta)
        desired_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial_root, desired_root)
    except Exception:
        shutil.rmtree(partial_root, ignore_errors=True)
        raise
    return desired_root / "retrieval_comparison.json"
