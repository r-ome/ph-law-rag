from __future__ import annotations

import json
import contextlib
import io
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals import artifacts
from app.evals.dataset import load_eval_dataset
from app.evals.generation_replay import _eval_row
from app.evals.integrity import file_sha256, sha256
from app.evals.paired_aggregate import build_paired_aggregate
from app.evals.ragas_scorer import scoring_identity
from app.evals.retrieval_runner import _capture_consistency, _storage_identities
from app.evals.runner import _git_sha
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.pipeline import stages
from app.pipeline.frozen_generation import generate_frozen
from app.pipeline.policy import resolve_policy
from app.pipeline.runner import prepare_answer_state
from app.pipeline.state import AnswerState
from app.retriever.adaptive_context import (
    ADAPTIVE_CONTEXT_CONTRACT_VERSION,
    ADAPTIVE_CONTEXT_DEFAULTS,
    ADAPTIVE_CONTEXT_TOKEN_ESTIMATOR,
    estimate_rendered_tokens,
    infer_structural_signals,
    packaging_pool_full_hash,
    packaging_pool_semantic_hash,
    select_adaptive_context,
)
from app.retriever.types import RetrievalResult


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


def _fixed_diagnostics(pool: list[RetrievalResult]) -> dict[str, Any]:
    rendered_tokens = estimate_rendered_tokens(pool)
    signals = infer_structural_signals(pool, accepted_legal_rewrite=False)
    return {
        "name": "adaptive_context",
        "enabled": False,
        "packaging_pool_semantic_hash": packaging_pool_semantic_hash(pool),
        "packaging_pool_full_hash": packaging_pool_full_hash(pool),
        "contract_version": ADAPTIVE_CONTEXT_CONTRACT_VERSION,
        "token_estimator": ADAPTIVE_CONTEXT_TOKEN_ESTIMATOR,
        "input_count": len(pool),
        "deduplicated_count": len(pool),
        "selected_count": len(pool),
        "cap": None,
        "rendered_tokens": rendered_tokens,
        "token_target": ADAPTIVE_CONTEXT_DEFAULTS["adaptive_context_token_target"],
        "token_overflow": max(
            0,
            rendered_tokens - ADAPTIVE_CONTEXT_DEFAULTS["adaptive_context_token_target"],
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
            "accepted_legal_rewrite": signals.accepted_legal_rewrite,
            "synthesis_detected": signals.synthesis_detected,
            "coverage_uncertain": signals.coverage_uncertain,
        },
    }


def _adaptive_diagnostics(
    pool: list[RetrievalResult],
    selected: list[RetrievalResult],
    detail: Any,
) -> dict[str, Any]:
    return {
        "name": "adaptive_context",
        "enabled": True,
        "packaging_pool_semantic_hash": packaging_pool_semantic_hash(pool),
        "packaging_pool_full_hash": packaging_pool_full_hash(pool),
        "selector": "adaptive",
        "source_rendered_tokens": estimate_rendered_tokens(pool),
        "source_selected_context_hash": sha256(_freeze(pool)),
        **detail.as_dict(),
        "selected_count": len(selected),
    }


def _frozen_row(
    *,
    item: dict[str, Any],
    selected: list[RetrievalResult],
    adaptive_context: dict[str, Any],
    state: AnswerState,
) -> dict[str, Any]:
    policy = state.policy or resolve_policy().policy
    selected_rows = _freeze(selected)
    return {
        "schema": {"name": "raglab.frozen-context", "major": 1, "minor": 1},
        "eval_id": item["id"],
        "question": item["question"],
        "effective_question": state.effective_question or item["question"],
        "selected_results": selected_rows,
        "selected_context_hash": sha256(selected_rows),
        "adaptive_context": adaptive_context,
        "retrieval_trace": {"stages": [adaptive_context]},
        "ground_truth": item.get("ground_truth", ""),
        "expected_sources": item.get("expected_sources", []),
        "category": item.get("category", ""),
        "split": item.get("split"),
        "facet": item.get("facet"),
        "topic": item.get("topic"),
        "retrieval_target_present": False,
        "model_choice": {
            "model": policy.generator_model,
            "reason": "policy_default",
        },
        "evidence": state.evidence.as_trace_dict() if state.evidence else None,
        "corrective_retrieval": {
            "ran": state.corrective_ran,
            "added_chunks": state.corrective_added_chunks,
            "baseline_selected_count": state.corrective_baseline_selected_count,
            "post_selected_count": state.corrective_post_selected_count,
            "max_added": state.corrective_max_added,
        },
        "policy": policy.as_trace_dict(),
    }


def _generate_row(frozen: dict[str, Any], *, model: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = generate_frozen(
        question=frozen["effective_question"],
        selected=frozen["selected_results"],
        model=model,
        later_enacted_preference=bool(
            frozen.get("policy", {}).get("later_enacted_preference_enabled")
        ),
        selfcheck_enabled=bool(frozen.get("policy", {}).get("selfcheck_enabled")),
    )
    return _eval_row(
        frozen,
        result,
        model_override=model,
        elapsed_s=time.perf_counter() - started,
    )


def _active_config(*, adaptive_context_enabled: bool) -> dict[str, Any]:
    from app.evals.runner import _active_config as base_active_config

    config = base_active_config()
    config["adaptive_context_enabled"] = adaptive_context_enabled
    return config


def _run_tag_exists(tag: str) -> bool:
    paths = artifacts.paths_for_tag(tag)
    return (
        (paths.run_dir is not None and paths.run_dir.exists())
        or paths.run.exists()
        or artifacts.existing_path(tag, "run") is not None
    )


def _write_run(
    *,
    tag: str,
    rows: list[dict[str, Any]],
    started_at: datetime,
    holdout: bool,
    splits: tuple[str, ...],
    active_config: dict[str, Any],
    storage: dict[str, Any],
    use_cache: bool,
) -> str:
    if _run_tag_exists(tag):
        raise FileExistsError(f"single-pass arm tag already exists: {tag}")
    paths = artifacts.create_run_paths(tag, started_at)
    if paths.run.exists():
        raise FileExistsError(f"single-pass arm tag already exists: {tag}")
    paths.run.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    model = rows[0].get("generator_model") if rows else resolve_policy().policy.generator_model
    meta = {
        "tag": tag,
        "date": started_at.strftime("%Y-%m-%d"),
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "profile": resolve_policy().policy.name,
        "model": model,
        "generator_model": model,
        "question_count": len(rows),
        "scored_count": None,
        "git_sha": _git_sha(),
        "active_config": active_config,
        "splits": list(splits),
        "holdout": holdout,
        "dataset_identity": {
            "path": settings.eval_dataset_path,
            "sha256": file_sha256(Path(settings.eval_dataset_path)),
            "row_count": len(rows),
            "splits": list(splits),
        },
        "scoring_identity": scoring_identity(generator_model=model, use_cache=use_cache),
        **storage,
    }
    artifacts.save_meta(tag, meta)
    artifacts.update_manifest(tag, meta=meta)
    return tag


def run_phase4_single_pass(
    *,
    tag: str,
    splits: tuple[str, ...],
    use_cache: bool = True,
) -> dict[str, Any]:
    if _run_tag_exists(f"{tag}-baseline"):
        raise FileExistsError(f"single-pass arm tag already exists: {tag}-baseline")
    if _run_tag_exists(f"{tag}-candidate"):
        raise FileExistsError(f"single-pass arm tag already exists: {tag}-candidate")
    paired_path = artifacts.results_dir() / "phase4_paired" / f"{tag}.json"
    if paired_path.exists():
        raise FileExistsError(f"paired aggregate tag already exists: {tag}")
    holdout = "holdout" in splits
    if holdout and tuple(splits) != ("holdout",):
        raise ValueError("holdout single-pass runs must use only the holdout split")
    rows = load_eval_dataset(splits=splits)
    policy = resolve_policy().policy
    retrieval_policy = replace(
        policy,
        retrieval_defaults=replace(
            policy.retrieval_defaults,
            adaptive_context_enabled=False,
        ),
    )
    started = datetime.now().astimezone()
    start_storage = _storage_identities()
    baseline_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        def run_one_row() -> tuple[dict[str, Any], dict[str, Any]]:
            collector = TraceCollector(capture_candidate_stages=True)
            state = AnswerState(
                item["question"],
                debug_enabled=True,
                policy=retrieval_policy,
            )
            with trace_context(trace_id=new_trace_id(), collector=collector):
                prepare_answer_state(state)
                if state.response is None:
                    stages.route_model(state)
            pool = list(state.selection.selected)
            fixed_diag = _fixed_diagnostics(pool)
            adaptive_selected, adaptive_detail = select_adaptive_context(
                pool,
                signals=infer_structural_signals(pool, accepted_legal_rewrite=False),
                floor=policy.retrieval_defaults.adaptive_context_floor,
                base_cap=policy.retrieval_defaults.adaptive_context_base_cap,
                uncertain_cap=policy.retrieval_defaults.adaptive_context_uncertain_cap,
                multifacet_cap=policy.retrieval_defaults.adaptive_context_multifacet_cap,
                stabilization_patience=(
                    policy.retrieval_defaults.adaptive_context_stabilization_patience
                ),
                token_target=policy.retrieval_defaults.adaptive_context_token_target,
            )
            adaptive_diag = _adaptive_diagnostics(
                pool,
                adaptive_selected,
                adaptive_detail,
            )
            baseline_frozen = _frozen_row(
                item=item,
                selected=pool,
                adaptive_context=fixed_diag,
                state=state,
            )
            candidate_frozen = _frozen_row(
                item=item,
                selected=adaptive_selected,
                adaptive_context=adaptive_diag,
                state=state,
            )
            return (
                _generate_row(baseline_frozen, model=policy.generator_model),
                _generate_row(candidate_frozen, model=policy.generator_model),
            )

        if holdout:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                baseline_row, candidate_row = run_one_row()
        else:
            baseline_row, candidate_row = run_one_row()
        baseline_rows.append(baseline_row)
        candidate_rows.append(candidate_row)
        if holdout:
            print(f"[{index}/{len(rows)}]", flush=True)
    end_storage = _storage_identities()
    storage = {
        **start_storage,
        "storage_consistency": _capture_consistency(start_storage, end_storage),
    }
    baseline_tag = _write_run(
        tag=f"{tag}-baseline",
        rows=baseline_rows,
        started_at=started,
        holdout=holdout,
        splits=splits,
        active_config=_active_config(adaptive_context_enabled=False),
        storage=storage,
        use_cache=use_cache,
    )
    candidate_tag = _write_run(
        tag=f"{tag}-candidate",
        rows=candidate_rows,
        started_at=started,
        holdout=holdout,
        splits=splits,
        active_config=_active_config(adaptive_context_enabled=True),
        storage=storage,
        use_cache=use_cache,
    )
    return build_paired_aggregate(
        baseline_tag,
        candidate_tag,
        tag=tag,
        use_cache=use_cache,
        holdout=holdout,
    )
