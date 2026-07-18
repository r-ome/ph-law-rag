"""Compare two sealed retrieval bundles without importing the retrieval stack."""

from __future__ import annotations

import os
import shutil
import json
import math
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

PHASE5_CP3_DECLARED_KNOB_DIFF = {
    "evidence_gate": ("min_chunks", "crag"),
    "corrective_retrieval_enabled": (False, True),
    "corrective_mode": ("append", "global_rerank"),
    # The locked Phase 4 control records the inert local judge as ``mistral``.
    "evidence_judge_model": ("mistral", "claude-haiku-4-5"),
    "corrective_max_facets": (None, 3),
    "corrective_facet_reserve_n": (None, 5),
}
PHASE5_CP3_IDENTITY_FIELDS = (
    "selected_context_hash",
    "context_block_hash",
    "source_map_hash",
    "system_prompt_hash",
    "user_prompt_hash",
)
PHASE5_CP3_CONTEXT_LIMITS = {
    "mean": 1509.3,
    "p95": 2649,
    "max": 3274,
    "new_overflow_count": 3,
    "soft_target": 2400,
}

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
    "adaptive_context_enabled",
    "adaptive_context_contract_version",
    "adaptive_context_floor",
    "adaptive_context_base_cap",
    "adaptive_context_uncertain_cap",
    "adaptive_context_multifacet_cap",
    "adaptive_context_stabilization_patience",
    "adaptive_context_token_target",
    "adaptive_context_token_estimator",
)
_ADAPTIVE_CONTEXT_INERT_KEYS = tuple(
    name
    for name in _SELECTION_KEYS
    if name.startswith("adaptive_context_")
    and name != "adaptive_context_enabled"
)
# Phase 5 CP2: evidence/corrective-mechanism knobs live at the top level of
# shared_values (AnswerPolicy fields, not RetrievalKnobs), not nested under
# retrieval_defaults. Declarable alongside _SELECTION_KEYS so a matched-arm
# comparison can assert an exact delta set spanning both families.
_EVIDENCE_KEYS = (
    "evidence_gate",
    "evidence_judge_model",
    "corrective_retrieval_enabled",
    "corrective_mode",
    "corrective_max_facets",
    "corrective_facet_reserve_n",
)
_QUERY_SEPARATION_ARMS = {"original_only", "original_plus_rewrite"}
_LEGACY_SIBLING_DEFAULTS = {
    "sibling_expansion_enabled": False,
    "sibling_expansion_radius": 1,
    "sibling_expansion_max_chars": 3000,
    "sibling_expansion_max_tokens": 750,
}
_LEGACY_CORRECTIVE_DEFAULTS = {
    "corrective_mode": "append",
    "corrective_max_facets": None,
    "corrective_facet_reserve_n": None,
}
_LEGACY_ADAPTIVE_DEFAULTS = {
    "adaptive_context_enabled": False,
    "adaptive_context_contract_version": 2,
    "adaptive_context_floor": 4,
    "adaptive_context_base_cap": 7,
    "adaptive_context_uncertain_cap": 11,
    "adaptive_context_multifacet_cap": 11,
    "adaptive_context_stabilization_patience": 2,
    "adaptive_context_token_target": 2400,
    "adaptive_context_token_estimator": "rendered_chars_div4_v1",
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
        if name not in _SELECTION_KEYS and name not in _EVIDENCE_KEYS:
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
    for name, default in _LEGACY_ADAPTIVE_DEFAULTS.items():
        defaults.setdefault(name, default)
    for name, default in _LEGACY_CORRECTIVE_DEFAULTS.items():
        comparable.setdefault(name, default)
    return comparable, profile


def _align_inactive_adaptive_context(
    baseline_shared: dict[str, Any],
    candidate_shared: dict[str, Any],
) -> None:
    """Canonicalize adaptive knobs that cannot affect a disabled arm.

    When exactly one arm enables adaptive packaging, its contract defines the
    comparison and the disabled arm inherits those inert values. When both arms
    are disabled, subordinate adaptive knobs are omitted from the comparison.
    With both enabled, every adaptive knob remains identity-bearing and strict.
    """
    baseline = baseline_shared["retrieval_defaults"]
    candidate = candidate_shared["retrieval_defaults"]
    baseline_enabled = bool(baseline.get("adaptive_context_enabled"))
    candidate_enabled = bool(candidate.get("adaptive_context_enabled"))
    if baseline_enabled and candidate_enabled:
        return
    if baseline_enabled != candidate_enabled:
        active = baseline if baseline_enabled else candidate
        inactive = candidate if baseline_enabled else baseline
        for name in _ADAPTIVE_CONTEXT_INERT_KEYS:
            inactive[name] = active.get(name)
        return
    for name in _ADAPTIVE_CONTEXT_INERT_KEYS:
        baseline.pop(name, None)
        candidate.pop(name, None)


def _selection_diff(
    baseline_shared: dict[str, Any],
    candidate_shared: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    baseline = _subset(baseline_shared["retrieval_defaults"], _SELECTION_KEYS)
    candidate = _subset(candidate_shared["retrieval_defaults"], _SELECTION_KEYS)
    diff = {
        name: (baseline[name], candidate[name])
        for name in _SELECTION_KEYS
        if baseline[name] != candidate[name]
    }
    baseline_evidence = _subset(baseline_shared, _EVIDENCE_KEYS)
    candidate_evidence = _subset(candidate_shared, _EVIDENCE_KEYS)
    diff.update(
        {
            name: (baseline_evidence[name], candidate_evidence[name])
            for name in _EVIDENCE_KEYS
            if baseline_evidence[name] != candidate_evidence[name]
        }
    )
    return diff


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
        if name in _EVIDENCE_KEYS:
            comparable.pop(name, None)
        else:
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
                "corrective_mode",
                "corrective_max_facets",
                "corrective_facet_reserve_n",
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


def _phase5_target_hash(
    rows: list[dict[str, Any]],
    baseline_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Bind CP3 target checks to the current sidecar and both sealed bundles."""
    from app.evals.retrieval_targets import load_retrieval_targets

    targets = load_retrieval_targets()
    eval_ids = [row["eval_id"] for row in rows]
    try:
        ordered_target_hash = ordered_hash([sha256(targets[eval_id]) for eval_id in eval_ids])
    except KeyError as exc:
        raise ValueError(f"CP3 target sidecar missing {exc.args[0]!r}") from exc
    expected = {"ordered_target_hash": ordered_target_hash}
    for arm, meta in (("baseline", baseline_meta), ("candidate", candidate_meta)):
        if meta.get("targets_identity") != expected:
            raise ValueError(f"CP3 target drift: {arm} bundle targets_identity mismatch")
    return targets, ordered_target_hash


def _load_cp1_partial_ids(
    audit_tag: str,
    *,
    baseline_meta: dict[str, Any],
) -> set[str]:
    """Load CP1's immutable firing population only after hash validation."""
    root = paths_for(audit_tag).root
    meta_path = root / "meta.json"
    rows_path = root / "facet_audit.jsonl"
    summary_path = root / "facet_audit_summary.json"
    if not meta_path.exists() or not rows_path.exists() or not summary_path.exists():
        raise FileNotFoundError(f"CP3 audit artifact {audit_tag!r} is incomplete")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("artifact_type") != "facet_audit":
        raise ValueError("CP3 audit artifact type mismatch")
    for path, key in ((rows_path, "rows_file_hash"), (summary_path, "summary_file_hash")):
        if meta.get(key) != file_sha256(path):
            raise ValueError(f"CP3 audit drift: {key} mismatch")
    if meta.get("source_bundle_file_hash") != baseline_meta.get("bundle_file_hash"):
        raise ValueError("CP3 audit drift: source bundle hash mismatch")
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != meta.get("row_count"):
        raise ValueError("CP3 audit drift: row count mismatch")
    return {row["eval_id"] for row in rows if row.get("verdict") == "partial"}


def _target_key(target: dict[str, Any], *, match_mode: str) -> str:
    if match_mode == "source_only":
        return f"source:{target['source_id']}"
    if target.get("unit_label"):
        return "leaf:{source_id}:{provision_id}:{unit_label}".format(**target)
    return "provision:{source_id}:{provision_id}".format(**target)


def _matched_final_targets(
    row: dict[str, Any], target_record: dict[str, Any],
) -> set[str]:
    """Return expected targets found in *final* selected results only."""
    selected = row.get("selected_results")
    if not isinstance(selected, list):
        raise ValueError(f"{row.get('eval_id')}: selected_results is missing")
    match_mode = target_record.get("match_mode")
    if match_mode not in {"exact", "source_only"}:
        raise ValueError(f"{row.get('eval_id')}: invalid target match mode")
    matched: set[str] = set()
    for target in target_record.get("targets", []):
        for result in selected:
            metadata = result.get("metadata") or {}
            if metadata.get("source_id") != target.get("source_id"):
                continue
            if match_mode == "source_only":
                matched.add(_target_key(target, match_mode=match_mode))
                break
            if metadata.get("provision_id") != target.get("provision_id"):
                continue
            # A leaf annotation is intentionally stricter than provision identity.
            if target.get("unit_label") and metadata.get("unit_label") != target["unit_label"]:
                continue
            matched.add(_target_key(target, match_mode=match_mode))
            break
    return matched


def _final_rendered_tokens(row: dict[str, Any], *, fired: bool) -> int:
    """Read the final adaptive selector diagnostic without accepting pass 1."""
    top_level = row.get("adaptive_context")
    stages = (row.get("retrieval_trace") or {}).get("stages")
    adaptive_stages = [
        stage for stage in stages or []
        if isinstance(stage, dict) and stage.get("name") == "adaptive_context"
    ]
    if isinstance(top_level, dict):
        if adaptive_stages:
            raise ValueError(f"{row.get('eval_id')}: derived adaptive context has trace stages")
        value = top_level.get("rendered_tokens")
    else:
        expected_count = 2 if fired else 1
        if not isinstance(stages, list) or len(adaptive_stages) != expected_count:
            raise ValueError(
                f"{row.get('eval_id')}: expected exactly {expected_count} direct adaptive stages"
            )
        fields = adaptive_stages[-1].get("fields")
        if not isinstance(fields, dict):
            raise ValueError(f"{row.get('eval_id')}: adaptive diagnostic fields are missing")
        value = fields.get("rendered_tokens")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{row.get('eval_id')}: invalid adaptive rendered_tokens")
    return int(value)


def _phase5_cp3_gates(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    baseline_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
    audit_tag: str,
) -> dict[str, Any]:
    """Mechanically enforce every CP3 retrieval-only gate before publication."""
    clean_worktree = candidate_meta.get("clean_worktree")
    if not isinstance(clean_worktree, dict) or clean_worktree.get("clean") is not True:
        raise ValueError("CP3 clean-worktree provenance is missing or unclean")
    targets, target_hash = _phase5_target_hash(baseline_rows, baseline_meta, candidate_meta)
    expected_fired = _load_cp1_partial_ids(audit_tag, baseline_meta=baseline_meta)
    baseline_by_id = {row["eval_id"]: row for row in baseline_rows}
    candidate_by_id = {row["eval_id"]: row for row in candidate_rows}
    candidate_fired = {
        eval_id for eval_id, row in candidate_by_id.items()
        if (row.get("corrective_retrieval") or {}).get("ran") is True
    }
    failures: list[str] = []

    if candidate_fired != expected_fired:
        failures.append("expected_firing_population")

    sufficient_mismatches: dict[str, list[str]] = {}
    for eval_id in sorted(set(baseline_by_id) - expected_fired):
        baseline, candidate = baseline_by_id[eval_id], candidate_by_id[eval_id]
        differing = [
            field for field in PHASE5_CP3_IDENTITY_FIELDS
            if (
                baseline.get(field) is None
                or candidate.get(field) is None
                or baseline.get(field) != candidate.get(field)
            )
        ]
        if differing:
            sufficient_mismatches[eval_id] = differing
    if sufficient_mismatches:
        failures.append("sufficient_row_identity")

    target_losses: dict[str, list[str]] = {}
    for eval_id in sorted(expected_fired):
        control = _matched_final_targets(baseline_by_id[eval_id], targets[eval_id])
        candidate = _matched_final_targets(candidate_by_id[eval_id], targets[eval_id])
        missing = sorted(control - candidate)
        if missing:
            target_losses[eval_id] = missing
    if target_losses:
        failures.append("fired_row_target_set_preservation")

    baseline_tokens: dict[str, int] = {}
    candidate_tokens: dict[str, int] = {}
    try:
        for eval_id in baseline_by_id:
            baseline_tokens[eval_id] = _final_rendered_tokens(
                baseline_by_id[eval_id], fired=False
            )
            candidate_tokens[eval_id] = _final_rendered_tokens(
                candidate_by_id[eval_id], fired=eval_id in expected_fired
            )
    except ValueError as exc:
        failures.append(f"context_diagnostic:{exc}")
    values = list(candidate_tokens.values())
    overflow_ids = {key for key, value in candidate_tokens.items() if value > PHASE5_CP3_CONTEXT_LIMITS["soft_target"]}
    control_overflow_ids = {key for key, value in baseline_tokens.items() if value > PHASE5_CP3_CONTEXT_LIMITS["soft_target"]}
    context_summary: dict[str, Any] = {}
    if values:
        sorted_values = sorted(values)
        mean = sum(values) / len(values)
        p95 = sorted_values[math.ceil(0.95 * len(values)) - 1]
        maximum = max(values)
        new_overflow = overflow_ids - control_overflow_ids
        context_summary = {
            "mean": mean, "p95": p95, "max": maximum,
            "newly_overflowing_ids": sorted(new_overflow),
            "resolved_overflow_ids": sorted(control_overflow_ids - overflow_ids),
            "fired_row_signed_deltas": {
                key: candidate_tokens[key] - baseline_tokens[key] for key in sorted(expected_fired)
            },
        }
        if (
            mean > PHASE5_CP3_CONTEXT_LIMITS["mean"]
            or p95 > PHASE5_CP3_CONTEXT_LIMITS["p95"]
            or maximum > PHASE5_CP3_CONTEXT_LIMITS["max"]
            or len(new_overflow) > PHASE5_CP3_CONTEXT_LIMITS["new_overflow_count"]
        ):
            failures.append("context_bounds")

    gates = {
        "clean_worktree_provenance": {"pass": True, "value": clean_worktree},
        "target_sidecar_hash": {"pass": True, "ordered_target_hash": target_hash},
        "expected_firing_population": {
            "pass": candidate_fired == expected_fired,
            "expected_ids": sorted(expected_fired), "observed_ids": sorted(candidate_fired),
        },
        "sufficient_row_identity": {"pass": not sufficient_mismatches, "mismatches": sufficient_mismatches},
        "fired_row_target_set_preservation": {"pass": not target_losses, "losses": target_losses},
        "context_bounds": {"pass": "context_bounds" not in failures, "limits": PHASE5_CP3_CONTEXT_LIMITS, **context_summary},
    }
    if failures:
        raise ValueError("Phase 5 CP3 gate failure: " + "; ".join(failures))
    return gates


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
    phase5_cp3_audit_tag: str | None = None,
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
    _align_inactive_adaptive_context(baseline_shared, candidate_shared)
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

    phase5_cp3_gates = None
    if phase5_cp3_audit_tag is not None:
        phase5_cp3_gates = _phase5_cp3_gates(
            baseline_rows,
            candidate_rows,
            baseline_meta=baseline_meta,
            candidate_meta=candidate_meta,
            audit_tag=phase5_cp3_audit_tag,
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
    if phase5_cp3_gates is not None:
        report["phase5_cp3_gates"] = phase5_cp3_gates

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


def compare_phase5_cp3_bundles(
    baseline_tag: str,
    candidate_tag: str,
    *,
    tag: str,
    facet_audit_tag: str = "phase5-cp1-facet-audit",
) -> Path:
    """Publish the CP3 retrieval-only comparator with its sealed gate contract."""
    return compare_retrieval_bundles(
        baseline_tag,
        candidate_tag,
        tag=tag,
        expected_arm_pair=("original_only", "original_only"),
        expected_knob_diff=PHASE5_CP3_DECLARED_KNOB_DIFF,
        phase5_cp3_audit_tag=facet_audit_tag,
    )
