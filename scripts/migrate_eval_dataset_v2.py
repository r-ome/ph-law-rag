#!/usr/bin/env python3
"""Mechanically migrate the reviewed eval dataset from v1 to v2.

This script intentionally never creates labels. It only merges the reviewed
backfill supplied by maintainers and refuses incomplete or inconsistent input.
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from app.evals.dataset import validate_rows
from app.retriever.intent_router import INTENTS


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data/eval_dataset.jsonl"
INTENT_LABELS_PATH = ROOT / "data/eval_intent_labels.jsonl"
BACKFILL_PATH = ROOT / "data/eval_backfill_labels.jsonl"
BACKUP_PATH = ROOT / "data/eval_dataset.v1.jsonl.bak"
HASH_PATH = ROOT / "data/eval_dataset.v1.sha256"
_FROZEN_CONTENT_KEYS = ("question", "ground_truth", "expected_sources", "category", "topic")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"required file is missing: {path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: row must be an object")
            row["__line__"] = line_no
            rows.append(row)
    return rows


def _index_by_question(rows: list[dict], path: Path, *, label: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        question = row.get("question")
        if not isinstance(question, str) or not question:
            raise ValueError(f"{path}:{row['__line__']}: missing non-empty question")
        if question in indexed:
            raise ValueError(f"{path}:{row['__line__']}: duplicate {label} question")
        indexed[question] = row
    return indexed


def _assert_exact_coverage(dataset_questions: set[str], joined: dict[str, dict], path: Path, label: str) -> None:
    missing = sorted(dataset_questions - set(joined))
    extra = sorted(set(joined) - dataset_questions)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {label} for {len(missing)} question(s): {missing[:3]}")
        if extra:
            parts.append(f"extra {label} for {len(extra)} question(s): {extra[:3]}")
        raise ValueError(f"{path}: " + "; ".join(parts))


def _enabled_source_ids() -> set[str]:
    from app.config import load_allowed_sources

    return {source.source_id for source in load_allowed_sources()}


def _frozen_hash(row: dict) -> str:
    """Digest of every evaluation-significant field, canonically serialized."""
    payload = json.dumps(
        {key: row[key] for key in _FROZEN_CONTENT_KEYS},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_frozen_content(v1_row: dict, output_row: dict, *, eval_id: str) -> None:
    """Ensure migration metadata cannot alter reviewed evaluation content."""
    changed = [key for key in _FROZEN_CONTENT_KEYS if output_row.get(key) != v1_row.get(key)]
    if changed:
        raise AssertionError(f"frozen-content guard failed for {eval_id}: {', '.join(changed)}")


def _write_hashes(rows: list[dict], path: Path) -> None:
    lines = [f"{row['id']} {_frozen_hash(row)}" for row in rows if row["split"] == "regression"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def migrate(
    dataset_path: Path = DATASET_PATH,
    intent_labels_path: Path = INTENT_LABELS_PATH,
    backfill_path: Path = BACKFILL_PATH,
    backup_path: Path = BACKUP_PATH,
    hash_path: Path = HASH_PATH,
) -> bool:
    v1_rows = _read_jsonl(dataset_path)
    if any("id" in row for row in v1_rows):
        print(f"{dataset_path} is already v2; no migration performed.")
        return False
    if len(v1_rows) != 81:
        raise ValueError(f"{dataset_path}: expected 81 v1 rows, found {len(v1_rows)}")

    v1_by_question = _index_by_question(v1_rows, dataset_path, label="dataset")
    dataset_questions = set(v1_by_question)
    intent_by_question = _index_by_question(_read_jsonl(intent_labels_path), intent_labels_path, label="intent")
    backfill_by_question = _index_by_question(_read_jsonl(backfill_path), backfill_path, label="backfill")
    _assert_exact_coverage(dataset_questions, intent_by_question, intent_labels_path, "intent labels")
    _assert_exact_coverage(dataset_questions, backfill_by_question, backfill_path, "backfill labels")

    output: list[dict] = []
    for index, v1_row in enumerate(v1_rows, start=1):
        question = v1_row["question"]
        intent_row = intent_by_question[question]
        backfill = backfill_by_question[question]
        expected_id = f"eval_{index:03d}"
        if backfill.get("id") != expected_id:
            raise ValueError(
                f"{backfill_path}:{backfill['__line__']}: id must be {expected_id!r}, got {backfill.get('id')!r}"
            )
        intent = intent_row.get("intent")
        if intent not in INTENTS:
            raise ValueError(f"{intent_labels_path}:{intent_row['__line__']}: invalid intent {intent!r}")
        if set(backfill) - {"__line__", "id", "question", "facet", "provisions", "difficulty"}:
            raise ValueError(f"{backfill_path}:{backfill['__line__']}: unexpected backfill field")

        item = {
            "id": expected_id,
            "split": "regression",
            **{key: v1_row[key] for key in _FROZEN_CONTENT_KEYS},
            "intent": intent,
            "facet": backfill.get("facet"),
            "provisions": backfill.get("provisions"),
            "difficulty": backfill.get("difficulty"),
        }
        assert_frozen_content(v1_row, item, eval_id=expected_id)
        output.append(item)

    validate_rows(output, _enabled_source_ids())
    # Validate a serialized candidate too, to catch accidental serialization changes.
    candidate_path = dataset_path.with_suffix(dataset_path.suffix + ".v2.tmp")
    candidate_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output), encoding="utf-8"
    )
    try:
        from app.evals.dataset import load_eval_dataset

        load_eval_dataset(candidate_path, splits=("regression",))
        shutil.copy2(dataset_path, backup_path)
        os.replace(candidate_path, dataset_path)
        _write_hashes(output, hash_path)
    finally:
        candidate_path.unlink(missing_ok=True)

    print(f"Migrated {len(output)} rows to v2; backup written to {backup_path}.")
    print("Review the migration, then delete data/eval_intent_labels.jsonl in the same PR.")
    return True


def main() -> int:
    try:
        migrate()
    except ValueError as exc:
        print(f"Eval dataset migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
