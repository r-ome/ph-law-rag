import importlib.util
import json
from pathlib import Path

import pytest


def _migration_module():
    path = Path(__file__).resolve().parents[2] / "scripts/migrate_eval_dataset_v2.py"
    spec = importlib.util.spec_from_file_location("migrate_eval_dataset_v2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixtures(tmp_path):
    v1 = [
        {
            "question": f"Question {index}?",
            "ground_truth": f"Answer {index}.",
            "expected_sources": ["constitution_1987"],
            "category": "factual",
            "topic": "constitutional_law",
        }
        for index in range(1, 82)
    ]
    intents = [{"question": row["question"], "intent": "default"} for row in v1]
    backfill = [
        {
            "id": f"eval_{index:03d}",
            "question": row["question"],
            "facet": "lookup",
            "provisions": [{"source_id": "constitution_1987", "cite": "Art. III"}],
            "difficulty": "easy",
        }
        for index, row in enumerate(v1, start=1)
    ]
    paths = {
        "dataset": tmp_path / "dataset.jsonl",
        "intents": tmp_path / "intents.jsonl",
        "backfill": tmp_path / "backfill.jsonl",
        "backup": tmp_path / "dataset.v1.jsonl.bak",
        "hashes": tmp_path / "dataset.v1.sha256",
    }
    _write_jsonl(paths["dataset"], v1)
    _write_jsonl(paths["intents"], intents)
    _write_jsonl(paths["backfill"], backfill)
    return paths, v1


def _migration_args(paths):
    return {
        "dataset_path": paths["dataset"],
        "intent_labels_path": paths["intents"],
        "backfill_path": paths["backfill"],
        "backup_path": paths["backup"],
        "hash_path": paths["hashes"],
    }


def test_migration_is_idempotent_and_writes_backup_and_hashes(tmp_path):
    module = _migration_module()
    paths, _ = _fixtures(tmp_path)

    assert module.migrate(**_migration_args(paths)) is True
    migrated = [json.loads(line) for line in paths["dataset"].read_text(encoding="utf-8").splitlines()]
    assert migrated[0]["id"] == "eval_001"
    assert migrated[-1]["id"] == "eval_081"
    assert paths["backup"].exists()
    assert len(paths["hashes"].read_text(encoding="utf-8").splitlines()) == 81
    assert module.migrate(**_migration_args(paths)) is False


def test_migration_fails_when_backfill_is_missing(tmp_path):
    module = _migration_module()
    paths, _ = _fixtures(tmp_path)
    paths["backfill"].unlink()

    with pytest.raises(ValueError, match="required file is missing"):
        module.migrate(**_migration_args(paths))


def test_frozen_content_guard_rejects_mutated_ground_truth():
    module = _migration_module()
    v1 = {"question": "Question?", "ground_truth": "Reviewed answer"}
    output = {"question": "Question?", "ground_truth": "Mutated answer"}

    with pytest.raises(AssertionError, match="ground_truth"):
        module.assert_frozen_content(v1, output, eval_id="eval_001")
