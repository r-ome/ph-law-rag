"""Canonical retrieval ground truth for non-holdout evaluation rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings

MATCH_MODES = frozenset({"exact", "source_only"})


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_no}: target record must be an object")
            record["__line__"] = line_no
            records.append(record)
    return records


def validate_retrieval_targets(
    records: list[dict[str, Any]],
    dataset_rows: list[dict[str, Any]],
    enabled_source_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Validate identity, coverage, source IDs, and canonical target shape."""
    dataset_by_id = {row["id"]: row for row in dataset_rows}
    expected_ids = {
        eval_id for eval_id, row in dataset_by_id.items() if row.get("split") != "holdout"
    }
    by_id: dict[str, dict[str, Any]] = {}

    for index, raw_record in enumerate(records, start=1):
        line_no = raw_record.get("__line__", index)
        record = {key: value for key, value in raw_record.items() if key != "__line__"}
        if set(record) != {"eval_id", "match_mode", "targets"}:
            raise ValueError(f"retrieval targets line {line_no}: invalid record keys")
        eval_id = record.get("eval_id")
        if not isinstance(eval_id, str) or not eval_id:
            raise ValueError(f"retrieval targets line {line_no}: missing eval_id")
        if eval_id in by_id:
            raise ValueError(f"retrieval targets line {line_no}: duplicate eval id {eval_id!r}")
        if eval_id not in dataset_by_id:
            raise ValueError(f"retrieval targets line {line_no}: unknown eval id {eval_id!r}")
        if dataset_by_id[eval_id].get("split") == "holdout":
            raise ValueError(f"retrieval targets line {line_no}: holdout target is prohibited")
        if record["match_mode"] not in MATCH_MODES:
            raise ValueError(f"retrieval targets line {line_no}: invalid match_mode")
        targets = record["targets"]
        if not isinstance(targets, list):
            raise ValueError(f"retrieval targets line {line_no}: targets must be a list")
        if dataset_by_id[eval_id].get("category") != "out-of-scope" and not targets:
            raise ValueError(f"retrieval targets line {line_no}: missing canonical targets")
        if dataset_by_id[eval_id].get("category") == "out-of-scope" and targets:
            raise ValueError(f"retrieval targets line {line_no}: out-of-scope targets must be empty")

        normalized_targets: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None, str | None]] = set()
        for target in targets:
            if not isinstance(target, dict) or not set(target).issubset(
                {"source_id", "provision_id", "unit_label"}
            ) or "source_id" not in target or "provision_id" not in target:
                raise ValueError(
                    f"retrieval targets line {line_no}: invalid canonical target"
                )
            source_id = target["source_id"]
            provision_id = target.get("provision_id")
            unit_label = target.get("unit_label")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError(f"retrieval targets line {line_no}: missing target source_id")
            if source_id not in enabled_source_ids:
                raise ValueError(
                    f"retrieval targets line {line_no}: unknown source_id {source_id!r}"
                )
            if record["match_mode"] == "exact" and (
                not isinstance(provision_id, str) or not provision_id
            ):
                raise ValueError(
                    f"retrieval targets line {line_no}: exact target requires provision_id"
                )
            if provision_id is not None and not isinstance(provision_id, str):
                raise ValueError(
                    f"retrieval targets line {line_no}: provision_id must be a string or null"
                )
            if unit_label is not None and (
                not isinstance(unit_label, str) or not unit_label
            ):
                raise ValueError(
                    f"retrieval targets line {line_no}: unit_label must be non-empty"
                )
            identity = (source_id, provision_id, unit_label)
            if identity in seen:
                continue
            seen.add(identity)
            normalized_targets.append(
                {
                    "source_id": source_id,
                    "provision_id": provision_id,
                    **({"unit_label": unit_label} if unit_label else {}),
                }
            )

        by_id[eval_id] = {
            "eval_id": eval_id,
            "match_mode": record["match_mode"],
            "targets": normalized_targets,
        }

    missing = sorted(expected_ids - set(by_id))
    if missing:
        raise ValueError(f"missing retrieval target eval IDs: {', '.join(missing)}")
    return by_id


def load_retrieval_targets(
    path: str | Path | None = None,
    *,
    dataset_rows: list[dict[str, Any]] | None = None,
    enabled_source_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if dataset_rows is None:
        from app.evals.dataset import load_eval_dataset

        dataset_rows = load_eval_dataset(splits=("regression", "dev"))
    if enabled_source_ids is None:
        from app.evals.dataset import _enabled_source_ids

        enabled_source_ids = _enabled_source_ids()
    target_path = Path(path or settings.eval_retrieval_targets_path)
    return validate_retrieval_targets(
        _read_jsonl(target_path), dataset_rows, enabled_source_ids
    )


def target_match_flags(
    *,
    source_id: str,
    provision_id: str,
    unit_label: str,
    target_record: dict[str, Any] | None,
) -> dict[str, bool | None]:
    targets = (target_record or {}).get("targets", [])
    source_match = any(target["source_id"] == source_id for target in targets)
    if (target_record or {}).get("match_mode") == "source_only":
        return {
            "expected_source_match": source_match,
            "expected_provision_match": None,
            "expected_leaf_match": None,
        }
    provision_match = any(
        target["source_id"] == source_id
        and target.get("provision_id") == provision_id
        for target in targets
    )
    leaf_targets = [target for target in targets if target.get("unit_label")]
    leaf_match = (
        any(
            target["source_id"] == source_id
            and target.get("provision_id") == provision_id
            and target.get("unit_label") == unit_label
            for target in leaf_targets
        )
        if leaf_targets
        else None
    )
    return {
        "expected_source_match": source_match,
        "expected_provision_match": provision_match,
        "expected_leaf_match": leaf_match,
    }
