import json

from scripts import classify_intent_ab as intent_ab


class FailingScorer:
    def score(self, question):
        raise AssertionError(f"unexpected cache miss for {question}")


def test_parse_prediction_accepts_strict_json_and_code_fences():
    assert intent_ab.parse_prediction('{"intent": "default", "confidence": "high"}') == (
        "default",
        "high",
    )
    assert intent_ab.parse_prediction(
        '```json\n{"intent": "out_of_scope", "confidence": "low"}\n```'
    ) == ("out_of_scope", "low")


def test_parse_prediction_rejects_extra_text_keys_and_unknown_values():
    assert intent_ab.parse_prediction('Here: {"intent": "default", "confidence": "high"}') is None
    assert intent_ab.parse_prediction(
        '{"intent": "default", "confidence": "high", "note": "extra"}'
    ) is None
    assert intent_ab.parse_prediction('{"intent": "case_law", "confidence": "high"}') is None
    assert intent_ab.parse_prediction('{"intent": "default", "confidence": "medium"}') is None


def test_cache_key_includes_rendered_prompt_and_question():
    base = intent_ab.cache_key("mistral", "mistral", "prompt v1", "question")

    assert intent_ab.cache_key("mistral", "mistral", "prompt v2", "question") != base
    assert intent_ab.cache_key("mistral", "mistral", "prompt v1", "other question") != base
    assert intent_ab.cache_key("mistral", "other-model", "prompt v1", "question") != base


def test_nli_cache_stores_scores_and_applies_threshold_at_scoring_time(tmp_path):
    question = "Did a newer law amend the old penalty?"
    key = intent_ab.cache_key("nli", "test-model", intent_ab.render_nli_prompt(), question)
    intent_ab._write_cache(
        tmp_path,
        key,
        {
            "scores": {
                "default": 0.0,
                "citation_lookup": -1.0,
                "list_or_rule_synthesis": -1.0,
                "amendment_or_current_law": 2.0,
                "out_of_scope": -1.0,
            }
        },
    )

    high = intent_ab.predict_nli(
        question,
        "test-model",
        tmp_path,
        margin_threshold=0.1,
        scorer=FailingScorer(),
    )
    low = intent_ab.predict_nli(
        question,
        "test-model",
        tmp_path,
        margin_threshold=0.95,
        scorer=FailingScorer(),
    )

    assert high["predicted_intent"] == "amendment_or_current_law"
    assert high["confidence"] == "high"
    assert high["routed_prediction"] == "amendment_or_current_law"
    assert low["predicted_intent"] == "amendment_or_current_law"
    assert low["confidence"] == "low"
    assert low["routed_prediction"] == "default"


def test_score_arm_reports_routed_and_raw_per_intent_metrics():
    rows = [
        {
            "gold": "citation_lookup",
            "predicted_intent": "citation_lookup",
            "routed_prediction": "citation_lookup",
            "parse_ok": True,
            "confidence": "high",
        },
        {
            "gold": "citation_lookup",
            "predicted_intent": "citation_lookup",
            "routed_prediction": "default",
            "parse_ok": True,
            "confidence": "low",
        },
        {
            "gold": "default",
            "predicted_intent": None,
            "routed_prediction": "default",
            "parse_ok": False,
            "confidence": None,
        },
    ]

    metrics = intent_ab.score_arm(rows)

    assert metrics["raw"]["accuracy"] == 2 / 3
    assert metrics["routed"]["accuracy"] == 2 / 3
    assert metrics["parse_failures"] == 1
    assert metrics["low_confidence"] == 1
    assert metrics["routed"]["per_intent"]["citation_lookup"] == {
        "precision": 1.0,
        "recall": 0.5,
        "support": 2,
    }


def test_confusion_matrix_is_gold_by_routed_prediction():
    rows = [
        {"gold": "default", "routed_prediction": "default"},
        {"gold": "default", "routed_prediction": "out_of_scope"},
        {"gold": "out_of_scope", "routed_prediction": "default"},
    ]

    matrix = intent_ab.confusion_matrix(rows, "routed_prediction")
    by_gold = {row["gold"]: row for row in matrix}

    assert by_gold["default"]["default"] == 1
    assert by_gold["default"]["out_of_scope"] == 1
    assert by_gold["out_of_scope"]["default"] == 1


def test_agreement_key_table_uses_unanimous_or_majority_correctness():
    arm_rows = {
        "mistral": [
            {"question": "q1", "gold": "default", "routed_prediction": "default"},
            {"question": "q2", "gold": "default", "routed_prediction": "citation_lookup"},
            {"question": "q3", "gold": "default", "routed_prediction": "default"},
        ],
        "haiku": [
            {"question": "q1", "gold": "default", "routed_prediction": "default"},
            {"question": "q2", "gold": "default", "routed_prediction": "citation_lookup"},
            {"question": "q3", "gold": "default", "routed_prediction": "out_of_scope"},
        ],
        "nli": [
            {"question": "q1", "gold": "default", "routed_prediction": "default"},
            {"question": "q2", "gold": "default", "routed_prediction": "citation_lookup"},
            {"question": "q3", "gold": "default", "routed_prediction": "default"},
        ],
    }

    stats = intent_ab.agreement_stats(arm_rows)

    assert stats["key_table"]["all_agree"] == {"correct": 1, "wrong": 1}
    assert stats["key_table"]["any_disagree"] == {"correct": 1, "wrong": 0}
    assert json.loads(json.dumps(stats))
