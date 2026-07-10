import json

import pytest

from app.evals.intent_labels import load_intent_labels, load_intent_labels_by_id


def _row(question: str, intent: str = "default") -> dict:
    return {
        "id": "eval_001",
        "split": "regression",
        "question": question,
        "ground_truth": "answer",
        "expected_sources": ["constitution_1987"],
        "category": "factual",
        "topic": "constitutional_law",
        "intent": intent,
        "facet": "lookup",
        "provisions": [{"source_id": "constitution_1987", "cite": "Art. III"}],
        "difficulty": "easy",
    }


def test_intent_loader_reads_v2_dataset_by_question_and_id(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(_row("known")) + "\n", encoding="utf-8")

    assert load_intent_labels(dataset) == {"known": "default"}
    assert load_intent_labels_by_id(dataset) == {"eval_001": "default"}


def test_intent_loader_rejects_invalid_intent(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(_row("known", "case_law_question")) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid intent"):
        load_intent_labels(dataset)
