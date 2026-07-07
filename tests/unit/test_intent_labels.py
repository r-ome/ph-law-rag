import json

import pytest

from app.evals.intent_labels import intent_counts, load_intent_labels


def test_eval_intent_labels_match_dataset_questions():
    labels = load_intent_labels()

    assert len(labels) == 81
    assert intent_counts(labels) == {
        "amendment_or_current_law": 13,
        "citation_lookup": 4,
        "default": 38,
        "list_or_rule_synthesis": 14,
        "out_of_scope": 12,
    }


def test_intent_label_loader_rejects_missing_and_extra_questions(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    labels = tmp_path / "labels.jsonl"
    dataset.write_text(
        json.dumps({"question": "known"}) + "\n",
        encoding="utf-8",
    )
    labels.write_text(
        json.dumps({"question": "other", "intent": "default"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing labels.*extra labels"):
        load_intent_labels(dataset, labels)


def test_intent_label_loader_rejects_invalid_intent(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    labels = tmp_path / "labels.jsonl"
    dataset.write_text(
        json.dumps({"question": "known"}) + "\n",
        encoding="utf-8",
    )
    labels.write_text(
        json.dumps({"question": "known", "intent": "case_law_question"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid intent"):
        load_intent_labels(dataset, labels)
