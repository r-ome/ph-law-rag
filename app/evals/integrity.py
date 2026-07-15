"""Lightweight integrity, compatibility, and durable-I/O helpers for eval bundles."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

SCHEMA_NAME = "raglab.frozen-context"
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 1
SUPPORTED_SCHEMA_MINORS = frozenset({0, 1})

QUERY_SEPARATION_CONTRACT_VERSION = 1
QUERY_SEPARATION_PROMPT_VERSION = "v3"
QUERY_SEPARATION_MAX_TOKENS = 160
_QUERY_SEPARATION_ASSISTANT_PREFILL = '{"legal_query":"'
_QUERY_SEPARATION_RESPONSE_RECONSTRUCTION = (
    "assistant_prefill + model_text_continuation"
)
_QUERY_SEPARATION_SYSTEM_PROMPT = (
    "You render a Philippine-law retrieval query.\n"
    "Your response is already prefilled with the exact bytes "
    "`{\"legal_query\":\"`. "
    "Continue after that prefill; do not repeat it. Together, the prefill and your "
    "continuation must form exactly one single-line raw JSON object and nothing "
    "else: no markdown fences, prose, leading/trailing whitespace, or line breaks. "
    "Use exactly three keys in this order: `legal_query` (a JSON string), "
    "`citations` (the empty JSON array `[]`), and `confidence` (a JSON string whose "
    "value is exactly `\"high\"` or `\"low\"`; never a number). Exact schema: "
    "`{\"legal_query\":\"...\",\"citations\":[],\"confidence\":\"high\"}`.\n"
    "Preserve the supplied source query verbatim at the start of `legal_query`, "
    "followed by ` | Legal terms: ` and one concise legal-language retrieval "
    "rendering. Never mention any statute number, act number, article number, "
    "section number, or case or docket number in the rendering. Describe the "
    "doctrine or legal concept in words instead. Never answer the question, "
    "invent a legal identifier, cite a source, offer alternatives, or use markdown."
)
_QUERY_SEPARATION_PROMPT_TEMPLATE = (
    "Source query (preserve it verbatim): {source_query}"
)


def schema_version(*, minor: int = SCHEMA_MINOR) -> dict[str, Any]:
    return {"name": SCHEMA_NAME, "major": SCHEMA_MAJOR, "minor": minor}


def validate_schema(value: Any) -> int:
    if not isinstance(value, dict):
        raise ValueError("artifact schema is missing")
    if value.get("name") != SCHEMA_NAME:
        raise ValueError(f"unsupported artifact schema {value.get('name')!r}")
    if value.get("major") != SCHEMA_MAJOR:
        raise ValueError(
            f"incompatible artifact schema major {value.get('major')!r}; "
            f"expected {SCHEMA_MAJOR}"
        )
    if type(value.get("minor")) is not int or value["minor"] < 0:
        raise ValueError("artifact schema minor must be a non-negative integer")
    if value["minor"] not in SUPPORTED_SCHEMA_MINORS:
        raise ValueError(
            f"unsupported frozen-context schema minor {value['minor']!r}; "
            f"supported minors are {sorted(SUPPORTED_SCHEMA_MINORS)}"
        )
    return value["minor"]


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ordered_hash(values: list[Any]) -> str:
    return sha256(values)


def query_separation_identity(*, arm: str = "original_only") -> dict[str, Any]:
    if arm not in {"original_only", "original_plus_rewrite"}:
        raise ValueError(f"unsupported query-separation arm {arm!r}")
    return {
        "arm": arm,
        "contract_version": QUERY_SEPARATION_CONTRACT_VERSION,
        "prompt_version": QUERY_SEPARATION_PROMPT_VERSION,
        "prompt_hash": sha256(
            {
                "contract_version": QUERY_SEPARATION_CONTRACT_VERSION,
                "prompt_version": QUERY_SEPARATION_PROMPT_VERSION,
                "system": _QUERY_SEPARATION_SYSTEM_PROMPT,
                "user_template": _QUERY_SEPARATION_PROMPT_TEMPLATE,
                "assistant_prefill": _QUERY_SEPARATION_ASSISTANT_PREFILL,
                "response_reconstruction": (
                    _QUERY_SEPARATION_RESPONSE_RECONSTRUCTION
                ),
            }
        ),
        "model": settings.legal_query_rewrite_model,
        "timeout_seconds": settings.legal_query_rewrite_timeout_seconds,
        "max_tokens": QUERY_SEPARATION_MAX_TOKENS,
    }


def retrieval_config_identity(
    shared_values: dict[str, Any],
    *,
    arm: str = "original_only",
) -> dict[str, Any]:
    shared_hash = sha256(shared_values)
    query_separation = query_separation_identity(arm=arm)
    return {
        "shared_values": shared_values,
        "shared_hash": shared_hash,
        "query_separation": query_separation,
        "full_hash": sha256(
            {
                "shared_hash": shared_hash,
                "query_separation": query_separation,
            }
        ),
    }


def adapt_retrieval_config(
    value: Any,
    *,
    schema_minor: int,
) -> dict[str, Any]:
    """Return the 1.1 identity shape for either a 1.0 or 1.1 bundle."""
    if not isinstance(value, dict):
        raise ValueError("retrieval_config is missing")
    if schema_minor == 0:
        if set(value) != {"values", "hash"} or not isinstance(value.get("values"), dict):
            raise ValueError("invalid schema 1.0 retrieval_config")
        if value.get("hash") != sha256(value["values"]):
            raise ValueError("schema 1.0 retrieval_config hash mismatch")
        adapted = retrieval_config_identity(value["values"], arm="original_only")
        # The legacy hash is the shared identity by definition.
        adapted["shared_hash"] = value["hash"]
        adapted["full_hash"] = sha256(
            {
                "shared_hash": value["hash"],
                "query_separation": adapted["query_separation"],
            }
        )
        return adapted
    if schema_minor != 1:
        raise ValueError("unsupported frozen-context schema minor")
    expected_keys = {
        "shared_values",
        "shared_hash",
        "query_separation",
        "full_hash",
    }
    if set(value) != expected_keys or not isinstance(value.get("shared_values"), dict):
        raise ValueError("invalid schema 1.1 retrieval_config")
    if value.get("shared_hash") != sha256(value["shared_values"]):
        raise ValueError("retrieval_config shared_hash mismatch")
    query_separation = value.get("query_separation")
    if not isinstance(query_separation, dict):
        raise ValueError("retrieval_config query_separation is missing")
    if set(query_separation) != {
        "arm",
        "contract_version",
        "prompt_version",
        "prompt_hash",
        "model",
        "timeout_seconds",
        "max_tokens",
    }:
        raise ValueError("invalid retrieval_config query_separation shape")
    if query_separation.get("arm") not in {
        "original_only",
        "original_plus_rewrite",
    }:
        raise ValueError("invalid retrieval_config query-separation arm")
    expected_full_hash = sha256(
        {
            "shared_hash": value["shared_hash"],
            "query_separation": query_separation,
        }
    )
    if value.get("full_hash") != expected_full_hash:
        raise ValueError("retrieval_config full_hash mismatch")
    return value


def _pre_rerank_pool_hash(
    candidate_stages: list[dict[str, Any]],
    *,
    schema_minor: int,
) -> str | None:
    if schema_minor == 0:
        snapshots = [
            snapshot for snapshot in candidate_stages if snapshot.get("stage") == "fused"
        ]
        if len(snapshots) != 1:
            raise ValueError("schema 1.0 requires exactly one legacy fused snapshot")
    elif schema_minor == 1:
        snapshots = [
            snapshot
            for snapshot in candidate_stages
            if snapshot.get("pool_role") == "pre_rerank_pool"
        ]
        if len(snapshots) != 1:
            raise ValueError(
                "schema 1.1 requires exactly one pool_role=pre_rerank_pool snapshot"
            )
        snapshot = snapshots[0]
        if snapshot.get("stage") != "fused" or snapshot.get("query_variant") != "combined":
            raise ValueError(
                "schema 1.1 pre_rerank_pool must be fused/combined"
            )
    else:
        raise ValueError("unsupported frozen-context schema minor")
    pool = [
        {
            "chunk_id": str(candidate.get("chunk_id", "")),
            "text_hash": text_sha256(str(candidate.get("text", ""))),
        }
        for candidate in snapshots[0].get("candidates", [])
    ]
    return ordered_hash(pool) if pool else None


def _legal_query_separation_semantic_input_hash(value: Any) -> str:
    if not isinstance(value, dict) or set(value) != {
        "arm",
        "source_query",
        "source_query_hash",
        "decision",
        "semantic_input_hash",
    }:
        raise ValueError("invalid legal_query_separation record")
    if value.get("arm") not in {"original_only", "original_plus_rewrite"}:
        raise ValueError("invalid legal_query_separation arm")
    source_query = value.get("source_query")
    if not isinstance(source_query, str) or value.get("source_query_hash") != text_sha256(
        source_query
    ):
        raise ValueError("legal_query_separation source query hash mismatch")
    decision = value.get("decision")
    expected_decision_keys = {
        "status",
        "legal_query",
        "legal_query_hash",
        "confidence",
        "parser_outcome",
        "fallback_reason",
        "model",
        "prompt_version",
        "prompt_hash",
        "raw_output_hash",
        "call_latency_ms",
        "cache_key",
        "cache_status",
    }
    if not isinstance(decision, dict) or set(decision) != expected_decision_keys:
        raise ValueError("invalid legal_query_separation decision")
    legal_query = decision.get("legal_query")
    expected_legal_query_hash = (
        text_sha256(legal_query) if isinstance(legal_query, str) else None
    )
    if decision.get("legal_query_hash") != expected_legal_query_hash:
        raise ValueError("legal_query_separation legal query hash mismatch")
    semantic_decision = {
        key: item
        for key, item in decision.items()
        if key not in {"call_latency_ms", "cache_status"}
    }
    expected = sha256(
        {
            "arm": value["arm"],
            "source_query": source_query,
            "source_query_hash": value["source_query_hash"],
            "decision": semantic_decision,
        }
    )
    if value.get("semantic_input_hash") != expected:
        raise ValueError("legal_query_separation semantic input hash mismatch")
    return expected


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenPaths:
    root: Path
    partial: Path
    sealed: Path
    trace: Path
    summary: Path
    state: Path
    meta: Path


def paths_for(
    tag: str,
    started_at: datetime | None = None,
    *,
    create: bool = False,
) -> FrozenPaths:
    if not tag or Path(tag).name != tag or tag in {".", ".."}:
        raise ValueError("artifact tag must be a single non-empty path component")
    runs = Path(settings.eval_results_dir) / "runs"
    root: Path | None = None
    if started_at is None and runs.exists():
        matches = sorted(
            date_dir / tag
            for date_dir in runs.iterdir()
            if date_dir.is_dir() and (date_dir / tag).is_dir()
        )
        root = matches[-1] if matches else None
    if root is None:
        started_at = started_at or datetime.now().astimezone()
        root = runs / started_at.strftime("%Y-%m-%d") / tag
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return FrozenPaths(
        root=root,
        partial=root / "frozen_contexts.partial.jsonl",
        sealed=root / "frozen_contexts.jsonl",
        trace=root / "retrieval_trace.jsonl",
        summary=root / "retrieval_summary.json",
        state=root / "retrieval_state.json",
        meta=root / "meta.json",
    )


def _record_without_hash(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "record_hash"}


def add_record_hash(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["record_hash"] = sha256(_record_without_hash(payload))
    return payload


def append_hashed_row(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    payload = add_record_hash(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def read_hashed_rows(
    path: Path,
    *,
    recover_trailing_fragment: bool = False,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    last_good_offset = 0
    with path.open("rb") as handle:
        line_number = 0
        while True:
            line = handle.readline()
            if not line:
                break
            line_number += 1
            end_offset = handle.tell()
            if not line.strip():
                last_good_offset = end_offset
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                is_last = handle.read(1) == b""
                if recover_trailing_fragment and is_last:
                    with path.open("r+b") as repair:
                        repair.truncate(last_good_offset)
                        repair.flush()
                        os.fsync(repair.fileno())
                    break
                raise ValueError(f"{path}:{line_number}: invalid frozen record: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: frozen record is not an object")
            validate_schema(row.get("schema"))
            expected = sha256(_record_without_hash(row))
            if row.get("record_hash") != expected:
                raise ValueError(f"{path}:{line_number}: record hash mismatch")
            rows.append(row)
            last_good_offset = end_offset
    eval_ids = [row.get("eval_id") for row in rows]
    if len(eval_ids) != len(set(eval_ids)):
        raise ValueError(f"{path}: duplicate eval_id in frozen records")
    return rows


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical_json(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def validate_sealed_bundle(
    paths: FrozenPaths,
    *,
    repair_state: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not paths.sealed.exists() or not paths.meta.exists() or not paths.state.exists():
        raise FileNotFoundError(f"retrieval bundle {paths.root} is not sealed")
    if not paths.trace.exists() or not paths.summary.exists():
        raise FileNotFoundError(f"retrieval bundle {paths.root} is missing derived artifacts")
    state = read_json(paths.state)
    meta = read_json(paths.meta)
    state_minor = validate_schema(state.get("schema"))
    meta_minor = validate_schema(meta.get("schema"))
    if state_minor != meta_minor:
        raise ValueError("retrieval bundle schema mismatch")
    if meta.get("artifact_type") != "retrieval_bundle":
        raise ValueError(f"retrieval bundle {paths.root} is not publishable")
    state_is_sealed = state.get("state") == "sealed"
    if not state_is_sealed and not repair_state:
        raise ValueError(f"retrieval bundle {paths.root} is not publishable")
    rows = read_hashed_rows(paths.sealed)
    rewrite_semantic_inputs: list[dict[str, Any]] = []
    for row in rows:
        row_minor = validate_schema(row.get("schema"))
        if row_minor != meta_minor:
            raise ValueError("retrieval bundle row schema mismatch")
        candidate_stages = row.get("candidate_stages")
        if not isinstance(candidate_stages, list):
            raise ValueError("retrieval bundle candidate_stages is missing")
        expected_pool_hash = _pre_rerank_pool_hash(
            candidate_stages,
            schema_minor=row_minor,
        )
        if row.get("pre_rerank_pool_hash") != expected_pool_hash:
            raise ValueError("retrieval bundle pre_rerank_pool_hash mismatch")
        legal_query_separation = row.get("legal_query_separation")
        if legal_query_separation is not None:
            rewrite_semantic_inputs.append(
                {
                    "eval_id": row["eval_id"],
                    "hash": _legal_query_separation_semantic_input_hash(
                        legal_query_separation
                    ),
                }
            )
    ordered_rewrite_key = (
        "ordered_legal_query_separation_semantic_input_hash"
    )
    if ordered_rewrite_key in meta and len(rewrite_semantic_inputs) != len(rows):
        raise ValueError("retrieval bundle legal_query_separation record is missing")
    record_hashes = [row["record_hash"] for row in rows]
    checks = {
        "row_count": len(rows),
        "ordered_record_hash": ordered_hash(record_hashes),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [
                {"eval_id": row["eval_id"], "hash": row.get("pre_rerank_pool_hash")}
                for row in rows
            ]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [
                {"eval_id": row["eval_id"], "hash": row["selected_context_hash"]}
                for row in rows
            ]
        ),
        "bundle_file_hash": file_sha256(paths.sealed),
        "retrieval_trace_hash": file_sha256(paths.trace),
        "retrieval_summary_hash": file_sha256(paths.summary),
    }
    if ordered_rewrite_key in meta:
        checks[ordered_rewrite_key] = ordered_hash(rewrite_semantic_inputs)
    for key, value in checks.items():
        if meta.get(key) != value or (state_is_sealed and state.get(key) != value):
            raise ValueError(f"retrieval bundle {key} mismatch")
    if [row["eval_id"] for row in rows] != meta.get("eval_ids"):
        raise ValueError("retrieval bundle eval order mismatch")
    if not state_is_sealed:
        publication_keys = (
            "row_count",
            "eval_ids",
            "ordered_record_hash",
            "ordered_pre_rerank_pool_hash",
            "ordered_selected_context_hash",
            "ordered_legal_query_separation_semantic_input_hash",
            "bundle_file_hash",
            "retrieval_trace_hash",
            "retrieval_summary_hash",
        )
        atomic_write_json(
            paths.state,
            {
                "schema": meta["schema"],
                "state": "sealed",
                **{
                    key: meta.get(key)
                    for key in publication_keys
                    if key in meta
                },
            },
        )
    return rows, meta
