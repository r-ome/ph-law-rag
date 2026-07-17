"""Offline-only replay of final-context packaging over a sealed schema 1.1 bundle."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.evals.frozen_contexts import seal
from app.evals.integrity import (
    adapt_retrieval_config,
    append_hashed_row,
    paths_for,
    read_json,
    sha256,
    text_sha256,
    validate_schema,
    validate_sealed_bundle,
)
from app.retriever.adaptive_context import (
    ADAPTIVE_CONTEXT_DEFAULTS,
    AdaptiveContextSignals,
    estimate_rendered_tokens,
    infer_structural_signals,
    select_adaptive_context,
)
from app.retriever.context_builder import build_context
from app.retriever.prompts import LATER_ENACTED_RULE, SYSTEM_PROMPT, build_user_prompt
from app.retriever.types import RetrievalResult

SelectorName = Literal["fixed", "adaptive"]


def _rehydrate(selected: list[dict[str, Any]]) -> list[RetrievalResult]:
    return [
        RetrievalResult(
            chunk_id=str(item["chunk_id"]),
            text=str(item.get("text", "")),
            score=float(item.get("score", 0.0)),
            metadata=dict(item.get("metadata", {}) or {}),
        )
        for item in selected
    ]


def _freeze(results: list[RetrievalResult]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": result.chunk_id,
            "text": result.text,
            "score": result.score,
            "metadata": dict(result.metadata),
        }
        for result in results
    ]


def _selected_stage(
    candidate_stages: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> None:
    indices = [
        index
        for index, snapshot in enumerate(candidate_stages)
        if snapshot.get("stage") == "selected"
    ]
    if not indices:
        raise ValueError("source row has no terminal selected candidate stage")
    index = indices[-1]
    snapshot = candidate_stages[index]
    source_by_id = {
        str(candidate.get("chunk_id")): candidate
        for candidate in snapshot.get("candidates", [])
    }
    candidates = []
    for rank, result in enumerate(selected, start=1):
        chunk_id = str(result["chunk_id"])
        if chunk_id not in source_by_id:
            raise ValueError(
                f"selected result {chunk_id!r} is absent from terminal candidate stage"
            )
        candidate = deepcopy(source_by_id[chunk_id])
        candidate.update(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "text": result.get("text", ""),
                "score": result.get("score", 0.0),
                "metadata": deepcopy(result.get("metadata", {}) or {}),
                "selected": True,
                "survived": True,
            }
        )
        candidates.append(candidate)
    snapshot["candidates"] = candidates


def _target_present(selected: list[dict[str, Any]], target: dict[str, Any] | None) -> bool:
    targets = (target or {}).get("targets", [])
    if not targets:
        return False
    for candidate in selected:
        metadata = candidate.get("metadata", {}) or {}
        for expected in targets:
            if metadata.get("source_id") != expected.get("source_id"):
                continue
            if (target or {}).get("match_mode") == "source_only" or (
                metadata.get("provision_id") == expected.get("provision_id")
            ):
                return True
    return False


def _recompute_evidence(row: dict[str, Any], selected_count: int) -> None:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("method") != "min_chunks":
        raise ValueError("offline context replay supports deterministic min_chunks evidence only")
    detail = evidence.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("source row evidence detail is missing")
    threshold = int(detail.get("min_chunks_for_answer", 0))
    if threshold < 1:
        raise ValueError("source row min_chunks_for_answer must be positive")
    prior_verdict = evidence.get("verdict")
    detail["selected_count"] = selected_count
    verdict = "sufficient" if selected_count >= threshold else "insufficient"
    evidence["verdict"] = verdict
    evidence["missing_facets"] = []
    if verdict != prior_verdict:
        raise ValueError("adaptive replay would change the evidence verdict")


def _assert_no_corrective_behavior(row: dict[str, Any]) -> None:
    corrective = row.get("corrective_retrieval") or {}
    if corrective.get("ran") or int(corrective.get("added_chunks") or 0):
        raise ValueError("adaptive replay refuses rows with corrective retrieval behavior")
    if any(
        snapshot.get("stage") == "corrective"
        for snapshot in row.get("candidate_stages", [])
    ):
        raise ValueError("adaptive replay refuses corrective candidate stages")


def _recompute_context_fields(row: dict[str, Any], selected: list[dict[str, Any]]) -> None:
    results = _rehydrate(selected)
    context, sources = build_context(results)
    later_enacted = bool(
        (row.get("policy") or {}).get("later_enacted_preference_enabled")
    )
    system = SYSTEM_PROMPT + (LATER_ENACTED_RULE if later_enacted else "")
    user = build_user_prompt(str(row["effective_question"]), context)
    row.update(
        {
            "context_block_hash": text_sha256(context),
            "source_map": sources,
            "source_map_hash": sha256(sources),
            "system_prompt_hash": text_sha256(system),
            "user_prompt_hash": text_sha256(user),
            "system_prompt": system,
            "user_prompt": user,
        }
    )
    terminal = row.get("terminal_response")
    if terminal is None:
        return
    if not isinstance(terminal, dict) or not terminal.get("abstained"):
        raise ValueError("offline selector replay refuses a generated terminal response")
    replacements = {
        "contexts": [result.text for result in results],
        "context_sources": [
            result.metadata.get("source_id", "") for result in results
        ],
        "context_block": context,
        "source_map": sources,
        "prompt": user,
        "system_prompt": system,
        "user_prompt": user,
    }
    for key, value in replacements.items():
        if key in terminal:
            terminal[key] = value


def _adaptive_signals(row: dict[str, Any], pool: list[RetrievalResult]) -> AdaptiveContextSignals:
    decision = (row.get("legal_query_separation") or {}).get("decision") or {}
    accepted_rewrite = decision.get("status") == "accepted"
    model_choice = row.get("model_choice") or {}
    synthesis_detected = model_choice.get("reason") in {
        "synthesis",
        "multi_facet",
        "planner_synthesis",
    }
    return infer_structural_signals(
        pool,
        accepted_legal_rewrite=accepted_rewrite,
        synthesis_detected=synthesis_detected,
    )


def _replay_row(
    source: dict[str, Any],
    *,
    selector: SelectorName,
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    row = deepcopy(source)
    _assert_no_corrective_behavior(row)
    source_selected = row.get("selected_results")
    if not isinstance(source_selected, list):
        raise ValueError("source row selected_results is missing")
    if row.get("selected_context_hash") != sha256(source_selected):
        raise ValueError("source row selected-context hash mismatch")
    if row.get("selection", {}).get("selected") != source_selected:
        raise ValueError("source row selection.selected differs from selected_results")

    pool = _rehydrate(source_selected)
    source_rendered_tokens = estimate_rendered_tokens(pool)
    if selector == "adaptive":
        selected_results, detail = select_adaptive_context(
            pool,
            signals=_adaptive_signals(row, pool),
        )
        diagnostics = detail.as_dict()
    else:
        selected_results = pool
        rendered_tokens = estimate_rendered_tokens(selected_results)
        diagnostics = {
            "contract_version": ADAPTIVE_CONTEXT_DEFAULTS[
                "adaptive_context_contract_version"
            ],
            "token_estimator": ADAPTIVE_CONTEXT_DEFAULTS[
                "adaptive_context_token_estimator"
            ],
            "input_count": len(pool),
            "deduplicated_count": len(pool),
            "selected_count": len(pool),
            "cap": None,
            "rendered_tokens": rendered_tokens,
            "token_target": ADAPTIVE_CONTEXT_DEFAULTS[
                "adaptive_context_token_target"
            ],
            "token_overflow": max(
                0,
                rendered_tokens
                - ADAPTIVE_CONTEXT_DEFAULTS["adaptive_context_token_target"],
            ),
            "chunk_cap_overflow": 0,
            "duplicate_chunk_ids_removed": 0,
            "represented_chunks_removed": 0,
            "duplicate_texts_removed": 0,
            "bundles_considered": 0,
            "bundles_selected": 0,
            "non_novel_bundles": 0,
            "stop_reason": "fixed_control",
            "signals": {
                "accepted_legal_rewrite": False,
                "synthesis_detected": False,
                "coverage_uncertain": False,
            },
        }

    selected = _freeze(selected_results)
    row["selection"]["selected"] = selected
    row["selected_results"] = selected
    row["selected_context_hash"] = sha256(selected)
    _selected_stage(row["candidate_stages"], selected)
    row["retrieval_target_present"] = _target_present(selected, target)
    _recompute_evidence(row, len(selected))
    _recompute_context_fields(row, selected)
    row["adaptive_context"] = {
        "selector": selector,
        "source_selected_context_hash": source["selected_context_hash"],
        "source_rendered_tokens": source_rendered_tokens,
        **diagnostics,
    }
    policy_defaults = (row.get("policy") or {}).get("retrieval_defaults")
    if isinstance(policy_defaults, dict):
        policy_defaults.update(ADAPTIVE_CONTEXT_DEFAULTS)
        policy_defaults["adaptive_context_enabled"] = selector == "adaptive"
    row.pop("record_hash", None)
    return row


def _derived_retrieval_config(
    source_config: dict[str, Any], *, selector: SelectorName
) -> dict[str, Any]:
    config = deepcopy(source_config)
    shared = config["shared_values"]
    defaults = shared.setdefault("retrieval_defaults", {})
    defaults.update(ADAPTIVE_CONTEXT_DEFAULTS)
    defaults["adaptive_context_enabled"] = selector == "adaptive"
    config["shared_hash"] = sha256(shared)
    config["full_hash"] = sha256(
        {
            "shared_hash": config["shared_hash"],
            "query_separation": config["query_separation"],
        }
    )
    return config


def replay_context_selection(
    source_tag: str,
    *,
    tag: str,
    selector: SelectorName = "adaptive",
) -> Path:
    """Publish a sealed derived bundle without retrieval, model, or SQLite calls."""
    if selector not in {"fixed", "adaptive"}:
        raise ValueError("selector must be 'fixed' or 'adaptive'")

    source_paths = paths_for(source_tag)
    source_meta_header = read_json(source_paths.meta)
    validate_schema(source_meta_header.get("schema"))
    if source_meta_header.get("holdout") or "holdout" in (
        source_meta_header.get("splits") or []
    ):
        raise ValueError("holdout is sealed and unavailable to context replay")
    source_rows, source_meta = validate_sealed_bundle(source_paths)
    source_minor = validate_schema(source_meta.get("schema"))
    if source_minor != 1:
        raise ValueError("adaptive context replay requires frozen-context schema 1.1")
    if (
        source_meta.get("holdout")
        or "holdout" in (source_meta.get("splits") or [])
        or any(row.get("split") == "holdout" for row in source_rows)
    ):
        raise ValueError("holdout is sealed and unavailable to context replay")
    if not source_rows:
        raise ValueError("context replay requires a non-empty source bundle")

    existing = paths_for(tag)
    if existing.root.exists():
        raise FileExistsError(f"retrieval tag already exists: {tag}")

    # Targets are regression/dev-only by contract. Load only after source and
    # destination validation, but before creating output artifacts.
    from app.evals.retrieval_targets import load_retrieval_targets

    all_targets = load_retrieval_targets()
    targets_by_id = {
        row["eval_id"]: all_targets[row["eval_id"]]
        for row in source_rows
        if row["eval_id"] in all_targets
    }
    missing_targets = [
        row["eval_id"]
        for row in source_rows
        if row.get("category") != "out-of-scope"
        and row["eval_id"] not in targets_by_id
    ]
    if missing_targets:
        raise ValueError(
            "source bundle is missing non-holdout targets: " + ", ".join(missing_targets)
        )

    derived_rows = [
        _replay_row(
            row,
            selector=selector,
            target=targets_by_id.get(row["eval_id"]),
        )
        for row in source_rows
    ]
    for source, derived in zip(source_rows, derived_rows):
        if source.get("pre_rerank_pool_hash") != derived.get("pre_rerank_pool_hash"):
            raise AssertionError("context replay changed a pre-rerank pool hash")
        if source.get("selection", {}).get("pre_expansion") != derived.get(
            "selection", {}
        ).get("pre_expansion"):
            raise AssertionError("context replay changed pre_expansion")

    source_config = adapt_retrieval_config(
        source_meta.get("retrieval_config"), schema_minor=source_minor
    )
    source_defaults = source_config["shared_values"].get("retrieval_defaults", {})
    if not isinstance(source_defaults, dict):
        raise ValueError("source retrieval_defaults identity is invalid")
    if source_defaults.get("adaptive_context_enabled", False):
        raise ValueError(
            "context replay requires an unselected or fixed-control packaging pool"
        )
    derived_config = _derived_retrieval_config(source_config, selector=selector)
    source_token_mean = sum(
        row["adaptive_context"]["source_rendered_tokens"] for row in derived_rows
    ) / len(derived_rows)
    selected_token_mean = sum(
        row["adaptive_context"]["rendered_tokens"] for row in derived_rows
    ) / len(derived_rows)
    mean_reduction = (
        (source_token_mean - selected_token_mean) / source_token_mean
        if source_token_mean
        else 0.0
    )
    changed_rows = sum(
        source["selected_context_hash"] != derived["selected_context_hash"]
        for source, derived in zip(source_rows, derived_rows)
    )
    started = datetime.now().astimezone()
    paths = paths_for(tag, started, create=True)
    hashed_rows = [append_hashed_row(paths.partial, row) for row in derived_rows]

    publication_keys = {
        "row_count",
        "eval_ids",
        "ordered_record_hash",
        "ordered_pre_rerank_pool_hash",
        "ordered_selected_context_hash",
        "ordered_legal_query_separation_semantic_input_hash",
        "bundle_file_hash",
        "retrieval_trace_hash",
        "retrieval_summary_hash",
    }
    meta = {
        key: deepcopy(value)
        for key, value in source_meta.items()
        if key not in publication_keys | {"schema", "artifact_type"}
    }
    meta.update(
        {
            "tag": tag,
            "date": started.strftime("%Y-%m-%d"),
            "started_at": started.isoformat(),
            "completed_at": datetime.now().astimezone().isoformat(),
            "retrieval_config": derived_config,
            "derived_from": {
                "tag": source_tag,
                "bundle_file_hash": source_meta.get("bundle_file_hash"),
                "ordered_selected_context_hash": source_meta.get(
                    "ordered_selected_context_hash"
                ),
                "selector_input": "selected_results",
            },
            "adaptive_context_experiment": {
                "selector": selector,
                **ADAPTIVE_CONTEXT_DEFAULTS,
                "adaptive_context_enabled": selector == "adaptive",
                "mean_reduction_watch_ceiling": 0.35,
                "source_rendered_tokens_mean": source_token_mean,
                "selected_rendered_tokens_mean": selected_token_mean,
                "mean_rendered_token_reduction": mean_reduction,
                "mean_reduction_watch_triggered": mean_reduction > 0.35,
                "changed_rows": changed_rows,
                "anti_inert_passed": changed_rows > 0,
            },
        }
    )
    seal(paths, meta=meta, rows=hashed_rows, targets_by_id=targets_by_id)
    return paths.sealed
