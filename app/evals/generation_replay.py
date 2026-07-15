"""Generation-only replay from a sealed retrieval bundle.

This module must remain importable without the retrieval stack. In particular,
do not import ``frozen_contexts``, ``runner``, ``stages``, or reranker modules.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals.integrity import (
    adapt_retrieval_config,
    add_record_hash,
    atomic_write_json,
    canonical_json,
    file_sha256,
    ordered_hash,
    paths_for,
    read_json,
    read_hashed_rows,
    schema_version,
    sha256,
    validate_schema,
    validate_sealed_bundle,
)
from app.pipeline.frozen_generation import replay_frozen


def _existing_generation_root(tag: str) -> Path | None:
    runs = Path(settings.eval_results_dir) / "runs"
    if not runs.exists():
        return None
    matches = sorted(
        date_dir / tag
        for date_dir in runs.iterdir()
        if date_dir.is_dir() and (date_dir / tag).is_dir()
    )
    return matches[-1] if matches else None


def _append_generation_row(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    payload = add_record_hash(row)
    with path.open("ab") as handle:
        handle.write(canonical_json(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _eval_row(
    frozen: dict[str, Any],
    result: dict[str, Any],
    *,
    model_override: str | None,
    elapsed_s: float,
) -> dict[str, Any]:
    model_choice = frozen.get("model_choice") or {
        "model": frozen.get("policy", {}).get("generator_model"),
        "reason": "not_generated",
    }
    effective_model = (
        model_override
        if model_override and not result.get("generation_skipped")
        else model_choice.get("model")
    )
    selected = frozen["selected_results"]
    return {
        "schema": schema_version(),
        "eval_id": frozen["eval_id"],
        "question": frozen["question"],
        "answer": result["answer"],
        "contexts": result.get("contexts", [item["text"] for item in selected]),
        "selected_chunk_ids": [item["chunk_id"] for item in selected],
        "debug_stages": frozen.get("retrieval_trace", {}).get("stages", []),
        "ground_truth": frozen.get("ground_truth", ""),
        "expected_sources": frozen.get("expected_sources", []),
        "category": frozen.get("category", ""),
        "split": frozen.get("split"),
        "facet": frozen.get("facet"),
        "topic": frozen.get("topic"),
        "abstained": bool(result.get("abstained")),
        "retrieval_target_present": bool(frozen.get("retrieval_target_present")),
        "profile": frozen.get("policy", {}).get("name"),
        "model": effective_model,
        "generator_model": effective_model,
        "model_choice": {**model_choice, "model": effective_model},
        "model_choice_reason": model_choice.get("reason"),
        "evidence": frozen.get("evidence"),
        "corrective_retrieval": frozen.get("corrective_retrieval", {}),
        "query_decomposition": bool(
            frozen.get("policy", {}).get("query_decomposition_enabled")
        ),
        "elapsed_s": round(elapsed_s, 2),
        "generation_skipped": bool(result.get("generation_skipped")),
        "cited_sources": [
            source.get("source_id", "") for source in result.get("sources", [])
        ],
        "context_sources": result.get("context_sources", []),
        "retrieved_sources": result.get("context_sources", []),
        "frozen_prompt_hashes": {
            key: frozen.get(key)
            for key in (
                "selected_context_hash",
                "context_block_hash",
                "source_map_hash",
                "system_prompt_hash",
                "user_prompt_hash",
            )
        },
    }


def generate_bundle(
    retrieval_tag: str,
    *,
    tag: str,
    model_override: str | None = None,
    resume: bool = False,
) -> Path:
    if not tag or Path(tag).name != tag or tag in {".", ".."}:
        raise ValueError("artifact tag must be a single non-empty path component")
    # Validate the source completely before creating a generation artifact.
    source_paths = paths_for(retrieval_tag)
    frozen_rows, retrieval_meta = validate_sealed_bundle(source_paths)
    retrieval_schema_minor = validate_schema(retrieval_meta.get("schema"))
    retrieval_config = adapt_retrieval_config(
        retrieval_meta.get("retrieval_config"),
        schema_minor=retrieval_schema_minor,
    )
    source_identity = {
        "tag": retrieval_tag,
        "bundle_file_hash": retrieval_meta["bundle_file_hash"],
        "ordered_record_hash": retrieval_meta["ordered_record_hash"],
        "retrieval_config_hash": retrieval_config["full_hash"],
        "generation_config_hash": retrieval_meta["generation_config"]["hash"],
        "corpus_hash": retrieval_meta["corpus_identity"]["hash"],
        "index_hash": retrieval_meta["index_identity"]["hash"],
    }
    replay_config = {
        "source_generation_config_hash": retrieval_meta["generation_config"]["hash"],
        "model_override": model_override,
    }
    generation_config_hash = sha256(replay_config)

    started = datetime.now().astimezone()
    existing_root = _existing_generation_root(tag)
    if not resume and existing_root is not None:
        raise FileExistsError(f"generation tag already exists: {tag}")
    if resume and existing_root is not None:
        root = existing_root
    else:
        root = Path(settings.eval_results_dir) / "runs" / started.strftime("%Y-%m-%d") / tag
    partial = root / "run.partial.jsonl"
    sealed = root / "run.jsonl"
    state_path = root / "generation_state.json"
    meta_path = root / "meta.json"
    root.mkdir(parents=True, exist_ok=True)

    state_base = {
        "schema": schema_version(),
        "tag": tag,
        "retrieval_source": source_identity,
        "generation_config_hash": generation_config_hash,
    }
    if resume and state_path.exists():
        previous_state = read_json(state_path)
        if (
            previous_state.get("retrieval_source") != source_identity
            or previous_state.get("generation_config_hash") != generation_config_hash
        ):
            raise ValueError("resume provenance changed for generation bundle")
    existing = (
        read_hashed_rows(partial, recover_trailing_fragment=True)
        if resume and partial.exists()
        else []
    )
    if resume and sealed.exists():
        existing = read_hashed_rows(sealed)
        existing_meta = read_json(meta_path)
        existing_state = read_json(state_path)
        validate_schema(existing_meta.get("schema"))
        validate_schema(existing_state.get("schema"))
        if (
            existing_meta.get("retrieval_source") != source_identity
            or existing_meta.get("generation_config_hash") != generation_config_hash
        ):
            raise ValueError("resume configuration does not match sealed generation bundle")
        checks = {
            "row_count": len(existing),
            "ordered_record_hash": ordered_hash(
                [row["record_hash"] for row in existing]
            ),
            "bundle_file_hash": file_sha256(sealed),
        }
        state_is_sealed = existing_state.get("state") == "sealed"
        if existing_meta.get("artifact_type") != "generation_bundle":
            raise ValueError("generation bundle is not sealed")
        for key, value in checks.items():
            if existing_meta.get(key) != value or (
                state_is_sealed and existing_state.get(key) != value
            ):
                raise ValueError(f"generation bundle {key} mismatch")
        if not state_is_sealed:
            atomic_write_json(
                state_path,
                {**state_base, "state": "sealed", **checks},
            )
        return sealed
    expected_prefix = [row["eval_id"] for row in frozen_rows[: len(existing)]]
    if [row["eval_id"] for row in existing] != expected_prefix:
        raise ValueError("partial generation bundle is not an ordered retrieval prefix")
    atomic_write_json(
        state_path,
        {**state_base, "state": "generating", "completed": len(existing)},
    )

    done = {row["eval_id"] for row in existing}
    try:
        for frozen in frozen_rows:
            if frozen["eval_id"] in done:
                continue
            row_started = time.perf_counter()
            result = replay_frozen(frozen, model_override=model_override)
            row = _eval_row(
                frozen,
                result,
                model_override=model_override,
                elapsed_s=time.perf_counter() - row_started,
            )
            _append_generation_row(partial, row)
            done.add(frozen["eval_id"])
            atomic_write_json(
                state_path,
                {**state_base, "state": "generating", "completed": len(done)},
            )
    except Exception as exc:
        atomic_write_json(
            state_path,
            {**state_base, "state": "failed", "completed": len(done), "error": str(exc)},
        )
        raise

    rows = read_hashed_rows(partial)
    if [row["eval_id"] for row in rows] != [row["eval_id"] for row in frozen_rows]:
        raise ValueError("generation bundle is incomplete or out of retrieval order")
    atomic_write_json(
        state_path,
        {**state_base, "state": "validating", "completed": len(rows)},
    )
    publication = {
        "row_count": len(rows),
        "ordered_record_hash": ordered_hash([row["record_hash"] for row in rows]),
        "bundle_file_hash": file_sha256(partial),
    }
    models = sorted({str(row.get("generator_model")) for row in rows})
    meta = {
        "schema": schema_version(),
        "artifact_type": "generation_bundle",
        "tag": tag,
        "date": started.strftime("%Y-%m-%d"),
        "started_at": started.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "retrieval_tag": retrieval_tag,
        "retrieval_source": source_identity,
        "generator_override": model_override,
        "parity_mode": model_override is None,
        "generation_config_hash": generation_config_hash,
        "question_count": len(rows),
        "scored_count": None,
        "holdout": False,
        "model": models[0] if len(models) == 1 else ",".join(models),
        "models": models,
        **publication,
    }
    atomic_write_json(meta_path, meta)
    os.replace(partial, sealed)
    atomic_write_json(
        state_path,
        {**state_base, "state": "sealed", **publication},
    )

    from app.evals import artifacts

    artifacts.update_manifest(tag, meta=meta)
    artifacts.write_latest(tag)
    return sealed
