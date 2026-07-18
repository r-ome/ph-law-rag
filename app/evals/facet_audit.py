"""Phase 5 CP1 — offline CRAG facet-checker audit over a sealed non-holdout bundle.

docs/retrieval_strategy_review.md, "# Phase 5 plan" > "## Checkpoints" > CP1.

Reuses the CRAG facet-checker prompt contract and parser verbatim from
``app.pipeline.evidence`` (never forked/reworded). Mirrors the paid-call cache
discipline of ``app.retriever.legal_query_rewriter`` exactly: content-addressed
cache key, O_EXCL pending marker, atomic finalization, cache hits never
re-call, ``anthropic.Anthropic(..., max_retries=0)``.

No serving-path behavior change: this module only reads sealed bundles and
writes a separate audit artifact under ``data/eval_results/runs``.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.evals.integrity import (
    atomic_write_json,
    canonical_json,
    file_sha256,
    paths_for,
    read_json,
    sha256,
    text_sha256,
    validate_sealed_bundle,
)
from app.retriever import facet_checker
from app.retriever.facet_checker import (
    FACET_AUDIT_CONTRACT_VERSION,
    FACET_AUDIT_MODEL,
    FacetAuditDecision,
    _cache_dir,
    _call_haiku,
    _render_crag_prompt,
    _write_pending,
    call_and_cache,
    cached_decision,
    facet_audit_cache_key,
    facet_audit_prompt_contract_hash,
)
from app.retriever.types import RetrievalResult

WATCH_ROW_IDS = ("eval_129", "eval_124")
CLASSIFICATION_METHOD = "token_coverage_v1"
CLASSIFICATION_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Pass-1 context reconstruction (refuse on hash mismatch, never rebuild).
# ---------------------------------------------------------------------------


def reconstruct_selected_context(row: dict[str, Any]) -> list[RetrievalResult]:
    eval_id = row.get("eval_id")
    selected = row.get("selected_results")
    if not isinstance(selected, list):
        raise ValueError(f"{eval_id}: row has no selected_results")
    if sha256(selected) != row.get("selected_context_hash"):
        raise ValueError(
            f"{eval_id}: selected_context_hash mismatch; refusing to rebuild context"
        )
    return [
        RetrievalResult(
            chunk_id=str(item["chunk_id"]),
            text=str(item.get("text", "")),
            score=float(item.get("score", 0.0)),
            metadata=dict(item.get("metadata", {}) or {}),
        )
        for item in selected
    ]


# ---------------------------------------------------------------------------
# Mechanical (a)/(b) classification against the sealed pre-rerank pool.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolChunk:
    chunk_id: str
    text: str


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
        "was", "were", "be", "been", "being", "that", "this", "these", "those",
        "it", "its", "as", "by", "at", "from", "with", "shall", "who", "which",
        "what", "when", "where", "how", "may", "must", "can", "could", "would",
        "should", "will", "not", "no", "if", "then", "than", "so", "such", "any",
        "all", "each", "other", "into", "under", "upon", "over", "between",
    }
)


def _normalize_tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS}


def _coverage(facet_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not facet_tokens:
        return 0.0
    return len(facet_tokens & chunk_tokens) / len(facet_tokens)


def _pool_chunks_from_snapshot(
    candidate_stages: list[dict[str, Any]], *, eval_id: str
) -> list[PoolChunk]:
    snapshots = [s for s in candidate_stages if s.get("pool_role") == "pre_rerank_pool"]
    if len(snapshots) != 1:
        raise ValueError(f"{eval_id}: expected exactly one pre_rerank_pool snapshot")
    candidates = snapshots[0].get("candidates", [])

    chunks: list[PoolChunk] = []
    fetch_ids: list[str] = []
    hash_by_id: dict[str, str] = {}
    for candidate in candidates:
        chunk_id = str(candidate.get("chunk_id", ""))
        text = candidate.get("text")
        if isinstance(text, str) and text:
            chunks.append(PoolChunk(chunk_id=chunk_id, text=text))
            continue
        text_hash = candidate.get("text_hash")
        if not isinstance(text_hash, str) or not text_hash:
            raise ValueError(
                f"{eval_id}: pool candidate {chunk_id} has neither text nor text_hash"
            )
        fetch_ids.append(chunk_id)
        hash_by_id[chunk_id] = text_hash

    if fetch_ids:
        from app.db import get_chunks_by_ids

        rows_by_id = {row["chunk_id"]: row["text"] for row in get_chunks_by_ids(fetch_ids)}
        for chunk_id in fetch_ids:
            text = rows_by_id.get(chunk_id)
            if text is None:
                raise ValueError(f"{eval_id}: chunk {chunk_id} not found in corpus (drift)")
            if text_sha256(text) != hash_by_id[chunk_id]:
                raise ValueError(
                    f"{eval_id}: chunk {chunk_id} text hash mismatch (corpus drift); refusing"
                )
            chunks.append(PoolChunk(chunk_id=chunk_id, text=text))
    return chunks


def classify_missing_facets(
    missing_facets: list[str],
    pool_chunks: list[PoolChunk],
    selected_chunk_ids: set[str],
    *,
    threshold: float = CLASSIFICATION_THRESHOLD,
) -> list[dict[str, Any]]:
    """Deterministic heuristic, not a gate input. See CLASSIFICATION_METHOD."""
    chunk_tokens = {chunk.chunk_id: _normalize_tokens(chunk.text) for chunk in pool_chunks}
    results: list[dict[str, Any]] = []
    for facet in missing_facets:
        facet_tokens = _normalize_tokens(facet)
        best_chunk_id: str | None = None
        best_coverage = 0.0
        for chunk in pool_chunks:
            coverage = _coverage(facet_tokens, chunk_tokens[chunk.chunk_id])
            if coverage > best_coverage:
                best_coverage = coverage
                best_chunk_id = chunk.chunk_id
        if best_chunk_id is None or best_coverage < threshold:
            classification = "absent_from_pool"  # (a)
        elif best_chunk_id not in selected_chunk_ids:
            classification = "dropped_by_selection"  # (b)
        else:
            # Present in the pool AND already in the selected context, yet the
            # judge flagged it missing: neither (a) nor (b) — judge noise.
            classification = "present_in_selected"
        results.append(
            {
                "facet": facet,
                "class": classification,
                "best_chunk_id": best_chunk_id,
                "coverage": round(best_coverage, 4),
                "method": CLASSIFICATION_METHOD,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Holdout fail-closed gate (before any cache/lock/artifact/network activity).
# ---------------------------------------------------------------------------


def _reject_if_holdout(bundle_tag: str) -> None:
    paths = paths_for(bundle_tag)
    if not paths.meta.exists():
        raise FileNotFoundError(f"retrieval bundle {bundle_tag!r} not found (no meta.json)")
    meta = read_json(paths.meta)
    if meta.get("holdout") is not False:
        raise PermissionError(
            f"bundle {bundle_tag!r} is holdout (or unmarked); CP1 access is forbidden"
        )
    splits = meta.get("splits")
    if not isinstance(splits, list) or "holdout" in splits:
        raise PermissionError(
            f"bundle {bundle_tag!r} includes a holdout split; CP1 access is forbidden"
        )


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def run_facet_audit(
    bundle_tag: str,
    *,
    output_tag: str,
    authorize_paid_calls: bool = False,
) -> dict[str, Any]:
    # Fail-closed holdout gate first: no cache dir, no output check, no bundle
    # row read beyond meta.json, before this passes.
    _reject_if_holdout(bundle_tag)

    output_root = paths_for(output_tag).root
    if output_root.exists():
        raise FileExistsError(f"facet audit tag already exists: {output_tag}")

    bundle_paths = paths_for(bundle_tag)
    rows, meta = validate_sealed_bundle(bundle_paths)
    if meta.get("holdout") is not False:
        raise PermissionError(f"bundle {bundle_tag!r} is holdout; CP1 access is forbidden")
    if any(row.get("split") == "holdout" for row in rows):
        raise PermissionError(
            f"bundle {bundle_tag!r} contains holdout rows; CP1 access is forbidden"
        )

    prepared: list[dict[str, Any]] = []
    for row in rows:
        eval_id = row["eval_id"]
        question = row.get("effective_question") or row["question"]
        selected = reconstruct_selected_context(row)
        rendered_prompt = _render_crag_prompt(question, selected)
        cache_key = facet_audit_cache_key(rendered_prompt, model=FACET_AUDIT_MODEL)
        cached = cached_decision(rendered_prompt, model=FACET_AUDIT_MODEL)
        prepared.append(
            {
                "row": row,
                "eval_id": eval_id,
                "selected": selected,
                "rendered_prompt": rendered_prompt,
                "cache_key": cache_key,
                "cached": cached,
            }
        )

    hits = sum(1 for item in prepared if item["cached"] is not None)
    misses = len(prepared) - hits

    if not authorize_paid_calls and misses:
        return {
            "mode": "dry_run",
            "bundle_tag": bundle_tag,
            "output_tag": output_tag,
            "row_count": len(prepared),
            "cache_hits": hits,
            "cache_misses": misses,
            "message": f"{misses} uncached Haiku calls required",
            "output_written": False,
        }

    decisions_by_id: dict[str, FacetAuditDecision] = {}
    calls_made = 0
    for item in prepared:
        decision = item["cached"]
        if decision is None:
            decision = call_and_cache(item["rendered_prompt"], model=FACET_AUDIT_MODEL)
            calls_made += 1
        decisions_by_id[item["eval_id"]] = decision

    rows_out: list[dict[str, Any]] = []
    class_counts = {"absent_from_pool": 0, "dropped_by_selection": 0, "present_in_selected": 0}
    partial_count = 0
    fallback_count = 0
    for item in prepared:
        row = item["row"]
        eval_id = item["eval_id"]
        decision = decisions_by_id[eval_id]
        selected_ids = {result.chunk_id for result in item["selected"]}
        pool_chunks = _pool_chunks_from_snapshot(row["candidate_stages"], eval_id=eval_id)
        classification = classify_missing_facets(decision.missing, pool_chunks, selected_ids)
        for entry in classification:
            class_counts[entry["class"]] += 1
        if decision.verdict == "partial":
            partial_count += 1
        if decision.operational_fallback:
            fallback_count += 1
        rows_out.append(
            {
                "eval_id": eval_id,
                "verdict": decision.verdict,
                "facets": decision.facets,
                "present": decision.present,
                "missing": decision.missing,
                "classification": classification,
                "cache_status": decision.cache_status,
                "operational_fallback": decision.operational_fallback,
            }
        )

    row_count = len(rows_out)
    partial_rate = partial_count / row_count if row_count else 0.0
    gate_pass = 0.05 <= partial_rate <= 0.35

    rows_by_id = {row["eval_id"]: row for row in rows}
    watch_rows: dict[str, Any] = {}
    for watch_id in WATCH_ROW_IDS:
        decision = decisions_by_id.get(watch_id)
        watch_rows[watch_id] = {
            "present_in_bundle": watch_id in rows_by_id,
            "verdict": decision.verdict if decision else None,
            "facets": decision.facets if decision else None,
            "missing": decision.missing if decision else None,
        }

    summary = {
        "artifact_type": "facet_audit_summary",
        "bundle_tag": bundle_tag,
        "output_tag": output_tag,
        "model": FACET_AUDIT_MODEL,
        "row_count": row_count,
        "partial_count": partial_count,
        "partial_rate": partial_rate,
        "gate": {"bounds": [0.05, 0.35], "pass": gate_pass, "enforced": False},
        "classification_counts": class_counts,
        "operational_fallback_count": fallback_count,
        "watch_rows": watch_rows,
        "cache": {"hits": hits, "misses": misses, "calls_made": calls_made},
    }

    started = datetime.now().astimezone()
    desired_root = paths_for(output_tag, started, create=False).root
    partial_root = desired_root.with_name(f".{desired_root.name}.{uuid4().hex}.partial")
    rows_path = partial_root / "facet_audit.jsonl"
    summary_path = partial_root / "facet_audit_summary.json"
    meta_path = partial_root / "meta.json"
    try:
        partial_root.mkdir(parents=True, exist_ok=True)
        with rows_path.open("wb") as handle:
            for record in rows_out:
                handle.write(canonical_json(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write_json(summary_path, summary)
        artifact_meta = {
            "artifact_type": "facet_audit",
            "bundle_tag": bundle_tag,
            "output_tag": output_tag,
            "date": started.strftime("%Y-%m-%d"),
            "started_at": started.isoformat(),
            "completed_at": datetime.now().astimezone().isoformat(),
            "holdout": False,
            "row_count": row_count,
            "rows_file_hash": file_sha256(rows_path),
            "summary_file_hash": file_sha256(summary_path),
            "source_bundle_file_hash": meta.get("bundle_file_hash"),
        }
        atomic_write_json(meta_path, artifact_meta)
        desired_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial_root, desired_root)
    except Exception:
        shutil.rmtree(partial_root, ignore_errors=True)
        raise

    return {
        "mode": "sealed",
        "output_path": str(desired_root),
        **summary,
    }
