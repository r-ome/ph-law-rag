import pytest

from app.config import settings
from app.retriever import answer_service, intent_router
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import resolve_knobs

pytestmark = pytest.mark.unit


def _capture_traces(monkeypatch):
    records = []

    class FakeTraceWriter:
        def write(self, record):
            records.append(record)

    monkeypatch.setattr(answer_service, "TraceWriter", lambda: FakeTraceWriter())
    monkeypatch.setattr(settings, "trace_logging_enabled", True)
    return records


def _response(answer="ok"):
    return {
        "answer": answer,
        "sources": [],
        "contexts": [],
        "context_sources": [],
        "abstained": False,
        "error": False,
    }


def test_answer_router_off_does_not_classify_and_traces_disabled_decision(monkeypatch):
    records = _capture_traces(monkeypatch)
    monkeypatch.setattr(settings, "router_enabled", False)

    def fail_classify(question):
        raise AssertionError("router should not classify when disabled")

    def fake_run_pipeline(question, debug_enabled, strategy_name, strategy_knobs):
        return _response(), SelectionResult(retrieved=[], pre_expansion=[], selected=[]), "prompt"

    monkeypatch.setattr(intent_router, "classify", fail_classify)
    monkeypatch.setattr(answer_service, "_run_pipeline", fake_run_pipeline)

    answer_service.answer("What is theft under Philippine law?", trace=True)

    assert len(records) == 1
    assert records[0]["intent_router"] == {
        "enabled": False,
        "model": None,
        "decision": None,
    }
    assert records[0]["retrieval_strategy"]["strategy"] == "default"


def test_answer_router_on_uses_current_law_strategy_knobs(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "router_enabled", True)
    monkeypatch.setattr(settings, "trace_logging_enabled", False)

    decision = intent_router.RouterDecision(
        intent="amendment_or_current_law",
        confidence="high",
        routed_intent="amendment_or_current_law",
        strategy="current_law",
        parse_ok=True,
        fallback_reason=None,
        error=None,
        latency_ms=1.0,
    )
    monkeypatch.setattr(intent_router, "classify", lambda question: decision)

    class FakeStrategy:
        name = "current_law"

        def execute(self, question, knobs=None):
            captured["question"] = question
            captured["knobs"] = knobs
            return SelectionResult(retrieved=[], pre_expansion=[], selected=[])

    monkeypatch.setitem(answer_service.STRATEGIES, "current_law", FakeStrategy())

    answer_service.answer("Which law controls after the amendment?", trace=False)

    assert captured["question"] == "Which law controls after the amendment?"
    assert captured["knobs"] == resolve_knobs("current_law")


def test_answer_strategy_override_skips_router_and_traces_reason(monkeypatch):
    records = _capture_traces(monkeypatch)
    captured = {}
    monkeypatch.setattr(settings, "router_enabled", True)

    def fail_classify(question):
        raise AssertionError("router should not classify when strategy is overridden")

    def fake_run_pipeline(question, debug_enabled, strategy_name, strategy_knobs):
        captured["strategy_name"] = strategy_name
        captured["knobs"] = strategy_knobs
        return _response(), SelectionResult(retrieved=[], pre_expansion=[], selected=[]), "prompt"

    monkeypatch.setattr(intent_router, "classify", fail_classify)
    monkeypatch.setattr(answer_service, "_run_pipeline", fake_run_pipeline)

    answer_service.answer("Which law controls after the amendment?", trace=True, strategy_override="current_law")

    assert captured["strategy_name"] == "current_law"
    assert captured["knobs"] == resolve_knobs("current_law")
    assert records[0]["intent_router"]["decision"] is None
    assert records[0]["intent_router"]["skipped_reason"] == "strategy_override"
    assert records[0]["retrieval_strategy"]["strategy"] == "current_law"


def test_answer_greeting_router_on_does_not_classify(monkeypatch):
    records = _capture_traces(monkeypatch)
    monkeypatch.setattr(settings, "router_enabled", True)

    def fail_classify(question):
        raise AssertionError("greeting should short-circuit before classify")

    monkeypatch.setattr(intent_router, "classify", fail_classify)

    response = answer_service.answer("hi", trace=True)

    assert response["answer"]
    assert len(records) == 1
    assert records[0]["intent_router"] == {
        "enabled": True,
        "model": settings.router_model,
        "decision": None,
    }
    assert records[0]["retrieval_strategy"]["strategy"] == "default"
