import hashlib

import pytest

from app.retriever import intent_router, strategy

pytestmark = pytest.mark.unit

_BENCHMARKED_PROMPT_SHA256 = "01056915c48c518f1a677980be4253e1f221103e3c5f29a8b98e3b060404a78a"


def _mock_generate(monkeypatch, result=None, error=None):
    def fake_generate(system_prompt, user_prompt, model=None):
        if error is not None:
            raise error
        return result

    monkeypatch.setattr("app.retriever.llm_client.generate", fake_generate)


def test_prompt_hash_matches_r1_benchmark():
    system, user = intent_router.render_llm_prompts("{question}")
    digest = hashlib.sha256(f"SYSTEM:\n{system}\nUSER:\n{user}".encode()).hexdigest()

    assert digest == _BENCHMARKED_PROMPT_SHA256


def test_parse_prediction_accepts_strict_json_and_code_fences():
    assert intent_router.parse_prediction('{"intent": "default", "confidence": "high"}') == (
        "default",
        "high",
    )
    assert intent_router.parse_prediction(
        '```json\n{"intent": "out_of_scope", "confidence": "low"}\n```'
    ) == ("out_of_scope", "low")


def test_parse_prediction_rejects_extra_text_keys_and_unknown_values():
    assert intent_router.parse_prediction('Here: {"intent": "default", "confidence": "high"}') is None
    assert intent_router.parse_prediction(
        '{"intent": "default", "confidence": "high", "note": "extra"}'
    ) is None
    assert intent_router.parse_prediction('{"intent": "case_law", "confidence": "high"}') is None
    assert intent_router.parse_prediction('{"intent": "default", "confidence": "medium"}') is None


def test_classify_routes_current_law_on_high_confidence(monkeypatch):
    _mock_generate(
        monkeypatch,
        result='{"intent": "amendment_or_current_law", "confidence": "high"}',
    )

    decision = intent_router.classify("Did a newer law change the penalty?")

    assert decision.intent == "amendment_or_current_law"
    assert decision.confidence == "high"
    assert decision.routed_intent == "amendment_or_current_law"
    assert decision.strategy == "current_law"
    assert decision.parse_ok is True
    assert decision.fallback_reason is None
    assert decision.error is None


def test_classify_with_raw_preserves_classifier_output(monkeypatch):
    raw = '{"intent": "default", "confidence": "high"}'
    _mock_generate(monkeypatch, result=raw)

    decision, returned_raw = intent_router.classify_with_raw("question")

    assert decision.routed_intent == "default"
    assert returned_raw == raw


def test_classify_low_confidence_preserves_intent_but_routes_default(monkeypatch):
    _mock_generate(
        monkeypatch,
        result='{"intent": "amendment_or_current_law", "confidence": "low"}',
    )

    decision = intent_router.classify("Did a newer law change it?")

    assert decision.intent == "amendment_or_current_law"
    assert decision.confidence == "low"
    assert decision.routed_intent == "default"
    assert decision.strategy == "default"
    assert decision.parse_ok is True
    assert decision.fallback_reason == "low_confidence"
    assert decision.error is None


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"intent": "default", "confidence": "high", "note": "extra"}',
        '{"intent": "case_law", "confidence": "high"}',
    ],
)
def test_classify_parse_failure_routes_default(monkeypatch, raw):
    _mock_generate(monkeypatch, result=raw)

    decision = intent_router.classify("question")

    assert decision.intent is None
    assert decision.confidence is None
    assert decision.routed_intent == "default"
    assert decision.strategy == "default"
    assert decision.parse_ok is False
    assert decision.fallback_reason == "parse_error"
    assert decision.error == "unparseable classifier output"


def test_classify_llm_error_routes_default(monkeypatch):
    from app.retriever.llm_client import LLMError

    _mock_generate(monkeypatch, error=LLMError("bad key"))

    decision = intent_router.classify("question")

    assert decision.routed_intent == "default"
    assert decision.strategy == "default"
    assert decision.parse_ok is False
    assert decision.fallback_reason == "llm_error"
    assert "LLMError: bad key" in decision.error


def test_classify_broad_exception_routes_default(monkeypatch):
    _mock_generate(monkeypatch, error=RuntimeError("backend exploded"))

    decision = intent_router.classify("question")

    assert decision.routed_intent == "default"
    assert decision.strategy == "default"
    assert decision.parse_ok is False
    assert decision.fallback_reason == "llm_error"
    assert "RuntimeError: backend exploded" in decision.error


def test_out_of_scope_is_label_only_default_strategy(monkeypatch):
    _mock_generate(
        monkeypatch,
        result='{"intent": "out_of_scope", "confidence": "high"}',
    )

    decision = intent_router.classify("How do I apply for a Canadian tourist visa?")

    assert decision.intent == "out_of_scope"
    assert decision.routed_intent == "out_of_scope"
    assert decision.strategy == "default"
    assert decision.fallback_reason is None


def test_intent_to_strategy_mapping_is_registered():
    assert set(intent_router.INTENT_TO_STRATEGY) == set(intent_router.INTENTS)
    assert set(intent_router.INTENT_TO_STRATEGY.values()) <= set(strategy.STRATEGIES)
