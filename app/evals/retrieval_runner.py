"""Run the production retrieval path and publish frozen contexts without generation."""

from __future__ import annotations

import sqlite3
import subprocess
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals.frozen_contexts import make_record, seal
from app.evals.integrity import (
    adapt_retrieval_config,
    append_hashed_row,
    atomic_write_json,
    file_sha256,
    ordered_hash,
    paths_for,
    read_json,
    read_hashed_rows,
    retrieval_config_identity,
    schema_version,
    sha256,
    text_sha256,
    validate_schema,
    validate_sealed_bundle,
)
from app.observability.context import TraceCollector, new_trace_id, trace_context
from app.pipeline.policy import AnswerPolicy, resolve_policy
from app.pipeline.runner import _build_trace_record, prepare_answer_state
from app.pipeline.state import AnswerState, LegalQuerySeparationArm


def _tree_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "files": [], "hash": None}
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    records = [
        {
            "path": str(item.relative_to(path.parent if path.is_file() else path)),
            "size": item.stat().st_size,
            "sha256": file_sha256(item),
        }
        for item in files
    ]
    return {"path": str(path), "files": records, "hash": ordered_hash(records)}


def _sqlite_corpus_identity() -> dict[str, Any]:
    path = Path(settings.db_path)
    if not path.exists():
        return {"path": str(path), "available": False, "hash": None}
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        versions = [
            list(row)
            for row in conn.execute(
                """
                SELECT d.source_id, v.version_id, v.content_hash
                FROM documents d
                JOIN document_versions v ON v.doc_id = d.doc_id
                ORDER BY d.source_id, v.fetched_at, v.version_id
                """
            )
        ]
        chunks = [
            {
                "chunk_id": row[0],
                "doc_id": row[1],
                "version_id": row[2],
                "text_hash": text_sha256(row[3]),
                "metadata_hash": text_sha256(row[4] or ""),
            }
            for row in conn.execute(
                """
                SELECT chunk_id, doc_id, version_id, text, metadata_json
                FROM chunks ORDER BY chunk_id
                """
            )
        ]
    finally:
        conn.close()
    identity = {
        "path": str(path),
        "available": True,
        "version_count": len(versions),
        "chunk_count": len(chunks),
        "versions_hash": ordered_hash(versions),
        "chunks_hash": ordered_hash(chunks),
    }
    identity["hash"] = sha256(identity)
    return identity


def _qdrant_collection_identity() -> dict[str, Any]:
    try:
        from app.indexing.index_service import get_qdrant_client

        info = get_qdrant_client().get_collection(settings.qdrant_collection)
        identity = {
            "available": True,
            "collection": settings.qdrant_collection,
            "status": str(getattr(info, "status", "")),
            "points_count": getattr(info, "points_count", None),
            "indexed_vectors_count": getattr(info, "indexed_vectors_count", None),
        }
    except Exception as exc:
        identity = {
            "available": False,
            "collection": settings.qdrant_collection,
            "warning": f"{type(exc).__name__}: {exc}",
        }
    # Preserve the diagnostic warning without allowing exception wording to
    # create false drift when Qdrant is unavailable at both boundaries.
    identity["hash"] = sha256(
        {key: value for key, value in identity.items() if key != "warning"}
    )
    return identity


def _storage_identities() -> dict[str, dict[str, Any]]:
    """Fingerprint every mutable retrieval store at one capture boundary."""
    corpus_identity = _sqlite_corpus_identity()
    bm25_identity = _tree_identity(Path(settings.bm25_path))
    qdrant_identity = _qdrant_collection_identity()
    index_identity = {
        "qdrant_collection": settings.qdrant_collection,
        "qdrant_url": settings.qdrant_url,
        "bm25_path": settings.bm25_path,
        "provision_supersession_path": settings.provision_supersession_path,
        "provision_status_path": settings.provision_status_path,
        "corpus_hash": corpus_identity["hash"],
        "bm25_hash": bm25_identity["hash"],
        "qdrant_hash": qdrant_identity["hash"],
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
    }
    index_identity["hash"] = sha256(index_identity)
    return {
        "corpus_identity": corpus_identity,
        "bm25_identity": bm25_identity,
        "qdrant_identity": qdrant_identity,
        "index_identity": index_identity,
    }


def _capture_consistency(
    start: dict[str, dict[str, Any]],
    end: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    keys = ("corpus_identity", "bm25_identity", "qdrant_identity", "index_identity")
    start_hashes = {key: start[key]["hash"] for key in keys}
    end_hashes = {key: end[key]["hash"] for key in keys}
    changed = [key for key in keys if start_hashes[key] != end_hashes[key]]
    return {
        "matched": not changed,
        "changed": changed,
        "start": start_hashes,
        "end": end_hashes,
    }


def _code_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    relative_files = [
        "app/pipeline/runner.py",
        "app/pipeline/stages.py",
        "app/pipeline/evidence.py",
        "app/pipeline/corrective.py",
        "app/retriever/hybrid_retriever.py",
        "app/retriever/legal_query_rewriter.py",
        "app/retriever/reranker.py",
        "app/retriever/context_selection.py",
        "app/retriever/sibling_expansion.py",
        "app/retriever/context_builder.py",
        "app/retriever/prompts.py",
    ]
    files = [
        {"path": relative, "sha256": file_sha256(root / relative)}
        for relative in relative_files
    ]
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        git_sha = None
    return {"git_sha": git_sha, "files": files, "hash": ordered_hash(files)}


def _config_identities(
    policy,
    *,
    query_separation_arm: LegalQuerySeparationArm = "original_only",
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy_data = policy.as_trace_dict()
    retrieval = {
        "profile": policy.name,
        "retrieval_defaults": policy_data["retrieval_defaults"],
        "query_decomposition_enabled": policy.query_decomposition_enabled,
        "query_rewriting_enabled": policy.query_rewriting_enabled,
        "evidence_gate": policy.evidence_gate,
        "evidence_judge_model": policy.evidence_judge_model,
        "min_chunks_for_answer": policy.min_chunks_for_answer,
        "corrective_retrieval_enabled": policy.corrective_retrieval_enabled,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "embedding_query_instruction": settings.embedding_query_instruction,
        "reranker_backend": settings.reranker_backend,
        "reranker_model": settings.reranker_model,
        "qwen3_reranker_model": settings.qwen3_reranker_model,
        "bedrock_rerank_model": settings.bedrock_rerank_model,
        "qdrant_collection": settings.qdrant_collection,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }
    generation = {
        "profile": policy.name,
        "generator_model": policy.generator_model,
        "strong_model": policy.strong_model,
        "router_enabled": policy.router_enabled,
        "router_model": policy.router_model,
        "escalate_intents": sorted(policy.escalate_intents),
        "escalate_on_partial_evidence": policy.escalate_on_partial_evidence,
        "selfcheck_enabled": policy.selfcheck_enabled,
        "later_enacted_preference_enabled": policy.later_enacted_preference_enabled,
    }
    return (
        retrieval_config_identity(retrieval, arm=query_separation_arm),
        {"values": generation, "hash": sha256(generation)},
    )


def _retrieval_target_present(record: dict[str, Any], target: dict[str, Any] | None) -> bool:
    targets = (target or {}).get("targets", [])
    if not targets:
        return False
    for snapshot in record["candidate_stages"]:
        if snapshot.get("stage") not in {"selected", "corrective"}:
            continue
        for candidate in snapshot.get("candidates", []):
            metadata = candidate.get("metadata", {}) or {}
            for expected in targets:
                if metadata.get("source_id") != expected.get("source_id"):
                    continue
                if (target or {}).get("match_mode") == "source_only" or (
                    metadata.get("provision_id") == expected.get("provision_id")
                ):
                    return True
    return False


def retrieve_rows(
    rows: list[dict[str, Any]],
    *,
    tag: str,
    resume: bool = False,
    keep_retrieval_models: bool = False,
    query_separation_arm: LegalQuerySeparationArm = "original_only",
    strategy_override: str | None = None,
) -> Path:
    if any(row.get("split") == "holdout" for row in rows):
        raise ValueError("holdout is sealed and unavailable to eval-retrieve")
    if not rows:
        raise ValueError("eval-retrieve requires at least one non-holdout row")
    if query_separation_arm not in {"original_only", "original_plus_rewrite"}:
        raise ValueError(f"unsupported query-separation arm {query_separation_arm!r}")

    policy = resolve_policy().policy
    if strategy_override is not None:
        from app.retriever.strategy import resolve_knobs

        policy = replace(
            policy,
            retrieval_defaults=resolve_knobs(strategy_override),
        )
    if policy.retrieval_defaults.subquery_packaging_enabled:
        raise ValueError(
            "schema 1.1 eval-retrieve requires subquery packaging to be disabled "
            "for every query-separation arm"
        )

    if query_separation_arm == "original_only":
        return _retrieve_rows_capture(
            rows,
            tag=tag,
            resume=resume,
            keep_retrieval_models=keep_retrieval_models,
            query_separation_arm=query_separation_arm,
            strategy_override=strategy_override,
            policy=policy,
        )

    # Import isolation is intentional: original-only capture and generation replay
    # must not load the paid-model rewrite module. The process lock is acquired
    # before retrieval artifacts are located or created and released by the
    # context manager even when capture fails.
    from app.retriever.legal_query_rewriter import legal_rewrite_capture

    with legal_rewrite_capture():
        return _retrieve_rows_capture(
            rows,
            tag=tag,
            resume=resume,
            keep_retrieval_models=keep_retrieval_models,
            query_separation_arm=query_separation_arm,
            strategy_override=strategy_override,
            policy=policy,
        )


def _retrieve_rows_capture(
    rows: list[dict[str, Any]],
    *,
    tag: str,
    resume: bool,
    keep_retrieval_models: bool,
    query_separation_arm: LegalQuerySeparationArm,
    strategy_override: str | None,
    policy: AnswerPolicy,
) -> Path:
    from app.evals.retrieval_targets import load_retrieval_targets

    all_targets = load_retrieval_targets()
    targets_by_id = {row["id"]: all_targets[row["id"]] for row in rows}
    dataset_identity = {
        "row_count": len(rows),
        "eval_ids": [row["id"] for row in rows],
        "ordered_row_hash": ordered_hash([sha256(row) for row in rows]),
    }
    targets_identity = {
        "ordered_target_hash": ordered_hash(
            [sha256(targets_by_id[row["id"]]) for row in rows]
        )
    }

    retrieval_config, generation_config = _config_identities(
        policy,
        query_separation_arm=query_separation_arm,
    )

    started = datetime.now().astimezone()
    existing_paths = paths_for(tag)
    if not resume and existing_paths.root.exists():
        raise FileExistsError(f"retrieval tag already exists: {tag}")
    paths = existing_paths if resume else paths_for(tag, started, create=True)
    if resume and paths.sealed.exists():
        _, meta = validate_sealed_bundle(paths, repair_state=True)
        if meta.get("dataset_identity") != dataset_identity:
            raise ValueError("resume dataset identity does not match sealed retrieval bundle")
        existing_minor = validate_schema(meta.get("schema"))
        existing_config = adapt_retrieval_config(
            meta.get("retrieval_config"),
            schema_minor=existing_minor,
        )
        if existing_config != retrieval_config:
            raise ValueError(
                "resume retrieval_config full identity does not match sealed bundle"
            )
        return paths.sealed
    if resume:
        paths.root.mkdir(parents=True, exist_ok=True)

    previous: dict[str, Any] | None = None
    if resume and paths.state.exists():
        previous = read_json(paths.state)
        previous_minor = validate_schema(previous.get("schema"))
        if previous_minor != schema_version()["minor"]:
            raise ValueError("cannot resume a partial retrieval bundle across schema minors")
        previous_retrieval_config = adapt_retrieval_config(
            previous.get("retrieval_config"),
            schema_minor=previous_minor,
        )
        if previous_retrieval_config != retrieval_config:
            raise ValueError("resume provenance changed: retrieval_config full identity")

    start_storage = _storage_identities()
    provenance = {
        "code_identity": _code_identity(),
        "dataset_identity": dataset_identity,
        "targets_identity": targets_identity,
        "retrieval_config": retrieval_config,
        "generation_config": generation_config,
        **start_storage,
    }
    state_base = {"schema": schema_version(), "tag": tag, **provenance}
    if previous is not None:
        for key in (
            "code_identity",
            "dataset_identity",
            "targets_identity",
            "generation_config",
            "corpus_identity",
            "index_identity",
        ):
            if previous.get(key) != provenance[key]:
                raise ValueError(f"resume provenance changed: {key}")
    atomic_write_json(paths.state, {**state_base, "state": "planned", "completed": 0})

    existing = (
        read_hashed_rows(paths.partial, recover_trailing_fragment=True)
        if resume
        else []
    )
    expected_prefix = [row["id"] for row in rows[: len(existing)]]
    if [row["eval_id"] for row in existing] != expected_prefix:
        raise ValueError("partial retrieval bundle is not an ordered dataset prefix")
    for frozen, source in zip(existing, rows):
        if frozen.get("dataset_row_hash") != sha256(source):
            raise ValueError(f"resume dataset row changed: {source['id']}")
    done = {row["eval_id"] for row in existing}
    atomic_write_json(
        paths.state,
        {**state_base, "state": "retrieving", "completed": len(existing)},
    )

    release_result: dict[str, Any] = {"attempted": False, "warning": None}
    try:
        for item in rows:
            if item["id"] in done:
                continue
            collector = TraceCollector(capture_candidate_stages=True)
            state = AnswerState(item["question"], debug_enabled=True, policy=policy)
            trace_id = new_trace_id()
            row_started = time.perf_counter()
            with trace_context(trace_id=trace_id, collector=collector):
                prepare_kwargs: dict[str, Any] = {
                    "query_separation_arm": query_separation_arm,
                }
                if strategy_override is not None:
                    prepare_kwargs["strategy_override"] = strategy_override
                prepare_answer_state(state, **prepare_kwargs)
                if state.response is None:
                    from app.pipeline import stages

                    stages.route_model(state)
                trace = _build_trace_record(
                    trace_id=trace_id,
                    trace_label="eval-retrieve",
                    state=state,
                    collector=collector,
                    elapsed_ms=(time.perf_counter() - row_started) * 1000,
                )
            record = make_record(state, trace)
            record["eval_id"] = item["id"]
            for key in (
                "ground_truth",
                "expected_sources",
                "category",
                "split",
                "facet",
                "topic",
            ):
                record[key] = item.get(key)
            record["dataset_row_hash"] = sha256(item)
            record["retrieval_target_present"] = _retrieval_target_present(
                record, targets_by_id.get(item["id"])
            )
            append_hashed_row(paths.partial, record)
            done.add(item["id"])
            atomic_write_json(
                paths.state,
                {**state_base, "state": "retrieving", "completed": len(done)},
            )
    except Exception as exc:
        atomic_write_json(
            paths.state,
            {**state_base, "state": "failed", "completed": len(done), "error": str(exc)},
        )
        raise
    finally:
        if not keep_retrieval_models:
            try:
                from app.retriever.reranker import release_retrieval_models

                release_result = release_retrieval_models()
            except Exception as exc:
                release_result = {
                    "attempted": True,
                    "warning": f"retrieval model release failed: {exc}",
                }
        else:
            release_result = {
                "attempted": False,
                "warning": "retrieval model release disabled for diagnostics",
            }

    final_rows = read_hashed_rows(paths.partial)
    if [row["eval_id"] for row in final_rows] != dataset_identity["eval_ids"]:
        atomic_write_json(
            paths.state,
            {**state_base, "state": "failed", "error": "incomplete or unordered bundle"},
        )
        raise ValueError("retrieval bundle is incomplete or out of dataset order")
    try:
        end_storage = _storage_identities()
    except Exception as exc:
        error = f"failed to fingerprint retrieval corpus/index after capture: {exc}"
        atomic_write_json(
            paths.state,
            {
                **state_base,
                "state": "failed",
                "completed": len(final_rows),
                "error": error,
            },
        )
        raise RuntimeError(error) from exc
    capture_consistency = _capture_consistency(start_storage, end_storage)
    if not capture_consistency["matched"]:
        error = (
            "retrieval corpus/index drifted during capture: "
            + ", ".join(capture_consistency["changed"])
        )
        atomic_write_json(
            paths.state,
            {
                **state_base,
                "state": "failed",
                "completed": len(final_rows),
                "error": error,
                "capture_consistency": capture_consistency,
                "end_storage": end_storage,
            },
        )
        raise ValueError(error)
    atomic_write_json(
        paths.state,
        {
            **state_base,
            "state": "validating",
            "completed": len(final_rows),
            "capture_consistency": capture_consistency,
        },
    )
    meta = {
        "tag": tag,
        "date": started.strftime("%Y-%m-%d"),
        "started_at": started.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "splits": sorted({row["split"] for row in rows}),
        "question_count": len(rows),
        "holdout": False,
        "model": policy.generator_model,
        "profile": policy.name,
        "memory_release": release_result,
        "capture_consistency": capture_consistency,
        "end_storage": end_storage,
        **provenance,
    }
    seal(paths, meta=meta, rows=final_rows, targets_by_id=targets_by_id)
    return paths.sealed
