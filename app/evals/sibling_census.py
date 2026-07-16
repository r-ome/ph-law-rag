"""Read-only census of exact-leaf misses recoverable by sibling expansion."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals.retrieval_targets import load_retrieval_targets
from app.evals.retrieval_trace import completed_sentinels, read_completed_trace


def _trace_bundle_is_holdout(trace_path: Path) -> bool:
    meta_path = trace_path.parent / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("sibling census bundle metadata is unreadable") from exc
    return bool(meta.get("holdout")) or "holdout" in (meta.get("splits") or [])


def _chunk_catalog(db_path: str | Path) -> tuple[dict[str, dict], dict[str, list[tuple[str, str]]]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT chunk_id, chunk_index, char_count, token_estimate, metadata_json
            FROM chunks
            WHERE json_extract(metadata_json, '$.parent_key') IS NOT NULL
              AND json_extract(metadata_json, '$.unit_label') IS NOT NULL
            ORDER BY json_extract(metadata_json, '$.parent_key'), chunk_index, chunk_id
            """
        ).fetchall()
    finally:
        conn.close()

    by_chunk: dict[str, dict] = {}
    family_members: dict[str, list[tuple[int, tuple[str, str]]]] = defaultdict(list)
    seen_members: set[tuple[str, tuple[str, str]]] = set()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        parent_key = str(metadata.get("parent_key") or "")
        unit_label = str(metadata.get("unit_label") or "")
        if not parent_key or not unit_label:
            continue
        identity = (parent_key, unit_label)
        record = {
            "chunk_id": str(row["chunk_id"]),
            "chunk_index": int(row["chunk_index"] or 0),
            "char_count": int(row["char_count"] or 0),
            "token_estimate": int(row["token_estimate"] or 0),
            "metadata": metadata,
            "leaf_identity": identity,
        }
        by_chunk[record["chunk_id"]] = record
        member_key = (parent_key, identity)
        if member_key not in seen_members:
            seen_members.add(member_key)
            family_members[parent_key].append((record["chunk_index"], identity))
    families = {
        parent_key: [identity for _, identity in sorted(members)]
        for parent_key, members in family_members.items()
    }
    return by_chunk, families


def build_sibling_eligibility_census(
    trace_path: str | Path,
    *,
    targets: dict[str, dict[str, Any]] | None = None,
    targets_path: str | Path | None = None,
    db_path: str | Path | None = None,
    radius: int = 1,
    small_sample_threshold: int = 6,
) -> dict[str, Any]:
    """Join sealed non-holdout traces to SQLite and count radius-eligible misses."""
    trace_path = Path(trace_path)
    records = read_completed_trace(trace_path)
    if _trace_bundle_is_holdout(trace_path) or any(
        record.get("split") == "holdout" for record in records
    ):
        raise ValueError("holdout traces are sealed and unavailable to sibling census")
    target_records = (
        targets if targets is not None else load_retrieval_targets(targets_path)
    )
    completed_eval_ids = {
        str(record["eval_id"])
        for record in completed_sentinels(trace_path)
        if record.get("eval_id")
    }
    by_chunk, families = _chunk_catalog(db_path or settings.db_path)

    by_eval: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_eval[str(record["eval_id"])].append(record)

    missed_rows: set[str] = set()
    eligible_rows: set[str] = set()
    recovered_expanded_rows: set[str] = set()
    recovered_selected_rows: set[str] = set()
    eligible_recovered_expanded_rows: set[str] = set()
    eligible_recovered_selected_rows: set[str] = set()
    details: list[dict[str, Any]] = []
    radius = max(0, int(radius))
    for eval_id, target_record in target_records.items():
        if eval_id not in completed_eval_ids:
            continue
        leaf_targets = [
            target
            for target in target_record.get("targets", [])
            if target.get("unit_label")
        ]
        if not leaf_targets:
            continue
        row_records = by_eval.get(eval_id, [])
        survivors = []
        seen_survivors: set[str] = set()
        for record in sorted(row_records, key=lambda item: int(item.get("rank", 0))):
            chunk_id = str(record.get("chunk_id", ""))
            if (
                record.get("stage") == "reranked"
                and record.get("survived")
                and chunk_id not in seen_survivors
            ):
                seen_survivors.add(chunk_id)
                survivors.append(record)
        survivor_leaf_keys = {
            (
                str(record.get("source_id", "")),
                str(record.get("provision_id", "")),
                str(record.get("unit_label", "")),
            )
            for record in survivors
        }
        sibling_expanded_leaf_keys = {
            (
                str(record.get("source_id", "")),
                str(record.get("provision_id", "")),
                str(record.get("unit_label", "")),
            )
            for record in row_records
            if record.get("stage") == "expanded"
            and record.get("expanded_from_sibling")
        }
        selected_leaf_keys = {
            (
                str(record.get("source_id", "")),
                str(record.get("provision_id", "")),
                str(record.get("unit_label", "")),
            )
            for record in row_records
            if record.get("stage") == "selected"
        }
        # Sibling expansion runs after parent expansion. The baseline expanded
        # snapshot therefore supplies the actual eligible seeds: whole-parent
        # replacements do not map to a child chunk and are naturally excluded.
        expanded_seeds = []
        seen_expanded: set[str] = set()
        for record in sorted(
            row_records,
            key=lambda item: (
                int(item.get("snapshot_ordinal", 0)),
                int(item.get("rank", 0)),
            ),
        ):
            chunk_id = str(record.get("chunk_id", ""))
            if (
                record.get("stage") == "expanded"
                and not record.get("expanded_from_sibling")
                and chunk_id not in seen_expanded
            ):
                seen_expanded.add(chunk_id)
                expanded_seeds.append(record)
        seed_catalog = [
            by_chunk[record["chunk_id"]]
            for record in expanded_seeds
            if record.get("chunk_id") in by_chunk
        ]

        for target in leaf_targets:
            target_key = (
                str(target.get("source_id", "")),
                str(target.get("provision_id", "")),
                str(target.get("unit_label", "")),
            )
            if target_key in survivor_leaf_keys:
                continue
            missed_rows.add(eval_id)
            target_chunks = [
                chunk
                for chunk in by_chunk.values()
                if (
                    str(chunk["metadata"].get("source_id", "")),
                    str(chunk["metadata"].get("provision_id", "")),
                    str(chunk["metadata"].get("unit_label", "")),
                )
                == target_key
            ]
            eligible_seed: dict | None = None
            sibling_offset: int | None = None
            for target_chunk in target_chunks:
                parent_key, _ = target_chunk["leaf_identity"]
                family = families.get(parent_key, [])
                if target_chunk["leaf_identity"] not in family:
                    continue
                target_index = family.index(target_chunk["leaf_identity"])
                for seed in seed_catalog:
                    if seed["leaf_identity"] not in family:
                        continue
                    offset = target_index - family.index(seed["leaf_identity"])
                    if 0 < abs(offset) <= radius:
                        eligible_seed = seed
                        sibling_offset = offset
                        break
                if eligible_seed is not None:
                    break
            eligible = eligible_seed is not None
            recovered_at_expanded = target_key in sibling_expanded_leaf_keys
            recovered_at_selected = recovered_at_expanded and target_key in selected_leaf_keys
            if eligible:
                eligible_rows.add(eval_id)
                if recovered_at_expanded:
                    eligible_recovered_expanded_rows.add(eval_id)
                if recovered_at_selected:
                    eligible_recovered_selected_rows.add(eval_id)
            if recovered_at_expanded:
                recovered_expanded_rows.add(eval_id)
            if recovered_at_selected:
                recovered_selected_rows.add(eval_id)
            details.append(
                {
                    "eval_id": eval_id,
                    "target": {
                        "source_id": target_key[0],
                        "provision_id": target_key[1],
                        "unit_label": target_key[2],
                    },
                    "eligible": eligible,
                    "seed_chunk_id": eligible_seed["chunk_id"] if eligible_seed else None,
                    "sibling_offset": sibling_offset,
                    "recovered_at_expanded": recovered_at_expanded,
                    "recovered_at_selected": recovered_at_selected,
                }
            )

    eligible_count = len(eligible_rows)
    return {
        "trace_path": str(trace_path),
        "radius": radius,
        "small_sample_threshold": small_sample_threshold,
        "missed_exact_leaf_rows": len(missed_rows),
        "eligible_missed_rows": eligible_count,
        "recovered_at_expanded_rows": len(recovered_expanded_rows),
        "recovered_at_selected_rows": len(recovered_selected_rows),
        "eligible_recovered_at_expanded_rows": len(
            eligible_recovered_expanded_rows
        ),
        "eligible_recovered_at_selected_rows": len(
            eligible_recovered_selected_rows
        ),
        "eligible_recovery_rate": (
            round(len(eligible_recovered_expanded_rows) / eligible_count, 4)
            if eligible_count
            else None
        ),
        "gate_mode": (
            "binding" if eligible_count >= small_sample_threshold else "descriptive"
        ),
        "eligible_eval_ids": sorted(eligible_rows),
        "eligible_recovered_eval_ids": sorted(eligible_recovered_expanded_rows),
        "details": details,
    }
