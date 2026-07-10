import importlib.util
import json
from pathlib import Path

import pytest

from app.evals import dataset


def _migration_module():
    path = Path(__file__).resolve().parents[2] / "scripts/migrate_eval_dataset_v2.py"
    spec = importlib.util.spec_from_file_location("migrate_eval_dataset_v2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**overrides) -> dict:
    row = {
        "id": "eval_001",
        "split": "regression",
        "question": "Question?",
        "ground_truth": "Answer.",
        "expected_sources": ["source_a"],
        "category": "factual",
        "topic": "topic",
        "intent": "default",
        "facet": "lookup",
        "provisions": [{"source_id": "source_a", "cite": "Sec. 1"}],
        "difficulty": "easy",
    }
    row.update(overrides)
    return row


def test_validation_rejects_invalid_schema_values():
    enabled = {"source_a"}
    with pytest.raises(ValueError, match="duplicate eval id"):
        dataset.validate_rows([_row(), _row(question="Other?")], enabled)
    with pytest.raises(ValueError, match="invalid facet"):
        dataset.validate_rows([_row(facet="maybe")], enabled)
    with pytest.raises(ValueError, match="unknown enabled source_id"):
        dataset.validate_rows([_row(expected_sources=["missing"])], enabled)
    with pytest.raises(ValueError, match="empty provisions"):
        dataset.validate_rows([_row(provisions=[])], enabled)
    with pytest.raises(ValueError, match="unknown key"):
        dataset.validate_rows([_row(extra="no")], enabled)


def test_validation_enforces_not_applicable_contract():
    enabled = {"source_a"}
    na = dict(
        category="out-of-scope", expected_sources=[], facet="not_applicable",
        provisions=[], difficulty="not_applicable",
    )
    dataset.validate_rows([_row(**na)], enabled)  # well-formed OOS row passes

    with pytest.raises(ValueError, match="missing required key"):
        rows = [_row(**na)]
        del rows[0]["provisions"]
        dataset.validate_rows(rows, enabled)
    with pytest.raises(ValueError, match="both be 'not_applicable' or neither"):
        dataset.validate_rows([_row(**{**na, "difficulty": "easy"})], enabled)
    with pytest.raises(ValueError, match="both be 'not_applicable' or neither"):
        dataset.validate_rows([_row(difficulty="not_applicable")], enabled)
    with pytest.raises(ValueError, match="reserved for category 'out-of-scope'"):
        dataset.validate_rows([_row(**{**na, "category": "ambiguous"})], enabled)
    with pytest.raises(ValueError, match="reserved for category 'out-of-scope'"):
        dataset.validate_rows([_row(category="out-of-scope")], enabled)
    with pytest.raises(ValueError, match="empty expected_sources"):
        dataset.validate_rows([_row(**{**na, "expected_sources": ["source_a"]})], enabled)


def test_split_filter_defaults_to_non_holdout(tmp_path, monkeypatch):
    path = tmp_path / "dataset.jsonl"
    rows = [_row(), _row(id="eval_002", question="Dev?", split="dev"), _row(id="eval_003", question="Holdout?", split="holdout")]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(dataset, "_enabled_source_ids", lambda: {"source_a"})

    assert [row["id"] for row in dataset.load_eval_dataset(path)] == ["eval_001", "eval_002"]
    assert [row["id"] for row in dataset.load_eval_dataset(path, splits=("holdout",))] == ["eval_003"]


def test_production_regression_content_matches_frozen_hashes():
    root = Path(__file__).resolve().parents[2]
    dataset_path = root / "data/eval_dataset.jsonl"
    hash_path = root / "data/eval_dataset.v1.sha256"
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or "id" not in rows[0]:
        pytest.skip("v2 migration pending reviewed data/eval_backfill_labels.jsonl")
    expected = {
        eval_id: digest
        for eval_id, digest in (line.split(maxsplit=1) for line in hash_path.read_text(encoding="utf-8").splitlines() if line)
    }
    frozen_hash = _migration_module()._frozen_hash  # single hash definition, no drift
    actual = {
        row["id"]: frozen_hash(row)
        for row in rows
        if row["split"] == "regression"
    }
    assert actual == expected
