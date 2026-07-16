"""Compare two sealed retrieval bundles without importing the retrieval stack."""

from __future__ import annotations

import os
import shutil
from copy import deepcopy
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
    sha256,
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
_QUERY_SEPARATION_ARMS = {"original_only", "original_plus_rewrite"}
_LEGACY_SIBLING_DEFAULTS = {
    "sibling_expansion_enabled": False,
    "sibling_expansion_radius": 1,
    "sibling_expansion_max_chars": 3000,
    "sibling_expansion_max_tokens": 750,
}


def _without_arm(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "arm"}


def _subset(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value.get(key) for key in keys}


def _normalize_expected_arm_pair(value: tuple[str, str]) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("expected_arm_pair must contain baseline and candidate arms")
    if any(arm not in _QUERY_SEPARATION_ARMS for arm in value):
        raise ValueError("expected_arm_pair contains an unsupported arm")
    return value


def _normalize_expected_knob_diff(
    value: dict[str, tuple[Any, Any]] | None,
) -> dict[str, tuple[Any, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("expected_knob_diff must be a mapping")
    normalized = {}
    for name, endpoints in value.items():
        if name not in _SELECTION_KEYS:
            raise ValueError(f"unknown retrieval selection knob {name!r}")
        if not isinstance(endpoints, (list, tuple)) or len(endpoints) != 2:
            raise ValueError(
                f"expected knob diff for {name!r} must contain two endpoints"
            )
        normalized[name] = (endpoints[0], endpoints[1])
    return normalized


def _comparable_shared_values(
    shared_values: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    comparable = deepcopy(shared_values)
    profile = comparable.pop("profile", None)
    defaults = comparable.setdefault("retrieval_defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("retrieval_defaults identity is invalid")
    for name, default in _LEGACY_SIBLING_DEFAULTS.items():
        defaults.setdefault(name, default)
    return comparable, profile


def _selection_diff(
    baseline_shared: dict[str, Any],
    candidate_shared: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    baseline = _subset(baseline_shared["retrieval_defaults"], _SELECTION_KEYS)
    candidate = _subset(candidate_shared["retrieval_defaults"], _SELECTION_KEYS)
    return {
        name: (baseline[name], candidate[name])
        for name in _SELECTION_KEYS
        if baseline[name] != candidate[name]
    }


def _require_expected_knob_diff(
    observed: dict[str, tuple[Any, Any]],
    declared: dict[str, tuple[Any, Any]],
) -> None:
    undeclared = sorted(set(observed) - set(declared))
    unobserved = sorted(set(declared) - set(observed))
    wrong_endpoints = sorted(
        name
        for name in set(observed) & set(declared)
        if observed[name] != declared[name]
    )
    if undeclared or unobserved or wrong_endpoints:
        details = []
        if undeclared:
            details.append("undeclared=" + ",".join(undeclared))
        if unobserved:
            details.append("unobserved=" + ",".join(unobserved))
        if wrong_endpoints:
            details.append("wrong_endpoints=" + ",".join(wrong_endpoints))
        raise ValueError(
            "retrieval comparison expected knob diff mismatch: " + "; ".join(details)
        )


def _without_declared_knobs(
    shared_values: dict[str, Any],
    declared: dict[str, tuple[Any, Any]],
) -> dict[str, Any]:
    comparable = deepcopy(shared_values)
    defaults = comparable["retrieval_defaults"]
    for name in declared:
        defaults.pop(name, None)
    return comparable


def _different_paths(
    baseline: Any,
    candidate: Any,
    *,
    path: str = "shared_values",
) -> list[str]:
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        differences = []
        for key in sorted(set(baseline) | set(candidate)):
            child = f"{path}.{key}"
            if key not in baseline or key not in candidate:
                differences.append(child)
            else:
                differences.extend(
                    _different_paths(baseline[key], candidate[key], path=child)
                )
        return differences
    if baseline != candidate:
        return [path]
    return []


def _report_knob_diff(
    value: dict[str, tuple[Any, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        name: {"baseline": endpoints[0], "candidate": endpoints[1]}
        for name, endpoints in value.items()
    }


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
    expected_arm_pair: tuple[str, str] = (
        "original_only",
        "original_plus_rewrite",
    ),
    expected_knob_diff: dict[str, tuple[Any, Any]] | None = None,
) -> Path:
    """Validate, compare, and durably publish a per-row retrieval report."""
    expected_arm_pair = _normalize_expected_arm_pair(expected_arm_pair)
    declared_knob_diff = _normalize_expected_knob_diff(expected_knob_diff)

    # Validate both sources completely before considering any output write.
    baseline_rows, baseline_meta, baseline_config = _validated_source(baseline_tag)
    candidate_rows, candidate_meta, candidate_config = _validated_source(candidate_tag)

    if _holdout_metadata(baseline_meta, baseline_rows) or _holdout_metadata(
        candidate_meta, candidate_rows
    ):
        raise ValueError("holdout retrieval bundles cannot be compared")

    baseline_arm = baseline_config["query_separation"].get("arm")
    candidate_arm = candidate_config["query_separation"].get("arm")
    if (baseline_arm, candidate_arm) != expected_arm_pair:
        raise ValueError(
            "retrieval comparison arm pair mismatch: "
            f"expected {expected_arm_pair!r}, "
            f"observed {(baseline_arm, candidate_arm)!r}"
        )
    if _without_arm(baseline_config["query_separation"]) != _without_arm(
        candidate_config["query_separation"]
    ):
        raise ValueError("retrieval comparison query-separation config mismatch")

    baseline_shared, baseline_profile = _comparable_shared_values(
        baseline_config["shared_values"]
    )
    candidate_shared, candidate_profile = _comparable_shared_values(
        candidate_config["shared_values"]
    )
    observed_knob_diff = _selection_diff(baseline_shared, candidate_shared)
    _require_expected_knob_diff(observed_knob_diff, declared_knob_diff)
    baseline_comparable = _without_declared_knobs(
        baseline_shared, declared_knob_diff
    )
    candidate_comparable = _without_declared_knobs(
        candidate_shared, declared_knob_diff
    )
    shared_value_mismatches = _different_paths(
        baseline_comparable, candidate_comparable
    )
    if shared_value_mismatches:
        raise ValueError(
            "retrieval comparison shared values mismatch: "
            + ", ".join(shared_value_mismatches)
        )
    baseline_comparable_hash = sha256(baseline_comparable)
    candidate_comparable_hash = sha256(candidate_comparable)
    if baseline_comparable_hash != candidate_comparable_hash:
        raise ValueError("retrieval comparison comparable_shared_hash mismatch")

    baseline_parts = _identity_parts(
        baseline_meta, {"shared_values": baseline_comparable}
    )
    candidate_parts = _identity_parts(
        candidate_meta, {"shared_values": candidate_comparable}
    )
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
        "expected_arm_pair": {
            "baseline": expected_arm_pair[0],
            "candidate": expected_arm_pair[1],
        },
        "declared_knob_diff": _report_knob_diff(declared_knob_diff),
        "observed_knob_diff": _report_knob_diff(observed_knob_diff),
        "shared_hash": baseline_config["shared_hash"],
        "shared_hash_alias": "baseline_raw_shared_hash",
        "baseline_raw_shared_hash": baseline_config["shared_hash"],
        "candidate_raw_shared_hash": candidate_config["shared_hash"],
        "comparable_shared_hash": baseline_comparable_hash,
        "profile_labels": {
            "baseline": baseline_profile,
            "candidate": candidate_profile,
            "matched": baseline_profile == candidate_profile,
            "severity": (
                "matched"
                if baseline_profile == candidate_profile
                else "informational"
            ),
            "affects_pass_fail": False,
        },
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
    existing_root = paths_for(tag).root
    if existing_root.exists():
        raise FileExistsError(f"retrieval comparison tag already exists: {tag}")
    desired_root = paths_for(tag, started, create=False).root
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
