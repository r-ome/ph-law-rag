import pytest

from app.config import settings
from app.pipeline import runner, stages
from app.retriever import answer_service, intent_router
from app.retriever.context_selection import SelectionResult
from app.retriever.llm_client import LLMError
from app.retriever.strategy import resolve_knobs
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _capture_traces(monkeypatch):
    records = []

    class FakeTraceWriter:
        def write(self, record):
            records.append(record)

    monkeypatch.setattr(runner, "TraceWriter", lambda: FakeTraceWriter())
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

    def fail_classify(question, model=None):
        raise AssertionError("router should not classify when disabled")

    def fake_retrieve_context(state):
        state.selection = SelectionResult(retrieved=[], pre_expansion=[], selected=[])

    def fake_gate_evidence(state):
        return None

    def fake_generate_answer(state):
        state.response = _response()
        state.prompt = "prompt"

    monkeypatch.setattr(intent_router, "classify", fail_classify)
    monkeypatch.setattr(stages, "retrieve_context", fake_retrieve_context)
    monkeypatch.setattr(stages, "gate_evidence", fake_gate_evidence)
    monkeypatch.setattr(stages, "generate_answer", fake_generate_answer)

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
    monkeypatch.setattr(intent_router, "classify", lambda question, model=None: decision)

    class FakeStrategy:
        name = "current_law"

        def execute(self, question, knobs=None, *, legal_query=None):
            captured["question"] = question
            captured["knobs"] = knobs
            return SelectionResult(retrieved=[], pre_expansion=[], selected=[])

    monkeypatch.setitem(stages.STRATEGIES, "current_law", FakeStrategy())

    answer_service.answer("Which law controls after the amendment?", trace=False)

    assert captured["question"] == "Which law controls after the amendment?"
    assert captured["knobs"] == resolve_knobs("current_law")


def test_answer_strategy_override_skips_router_and_traces_reason(monkeypatch):
    records = _capture_traces(monkeypatch)
    captured = {}
    monkeypatch.setattr(settings, "router_enabled", True)

    def fail_classify(question, model=None):
        raise AssertionError("router should not classify when strategy is overridden")

    def fake_retrieve_context(state):
        captured["strategy_name"] = state.strategy_name
        captured["knobs"] = state.strategy_knobs
        state.selection = SelectionResult(retrieved=[], pre_expansion=[], selected=[])

    def fake_gate_evidence(state):
        return None

    def fake_generate_answer(state):
        state.response = _response()
        state.prompt = "prompt"

    monkeypatch.setattr(intent_router, "classify", fail_classify)
    monkeypatch.setattr(stages, "retrieve_context", fake_retrieve_context)
    monkeypatch.setattr(stages, "gate_evidence", fake_gate_evidence)
    monkeypatch.setattr(stages, "generate_answer", fake_generate_answer)

    answer_service.answer("Which law controls after the amendment?", trace=True, strategy_override="current_law")

    assert captured["strategy_name"] == "current_law"
    assert captured["knobs"] == resolve_knobs("current_law")
    assert records[0]["intent_router"]["decision"] is None
    assert records[0]["intent_router"]["skipped_reason"] == "strategy_override"
    assert records[0]["retrieval_strategy"]["strategy"] == "current_law"


def test_cascade_profile_routes_generation_to_strong_model(monkeypatch):
    records = _capture_traces(monkeypatch)
    generated = {}
    monkeypatch.setattr(settings, "raglab_profile", "cascade")

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
    monkeypatch.setattr(intent_router, "classify", lambda question, model=None: decision)

    class FakeStrategy:
        name = "current_law"

        def execute(self, question, knobs=None, *, legal_query=None):
            return SelectionResult(
                retrieved=[],
                pre_expansion=[RetrievalResult("c1", "context", 1.0, {})],
                selected=[RetrievalResult("c1", "context", 1.0, {})],
            )

    monkeypatch.setitem(stages.STRATEGIES, "current_law", FakeStrategy())
    monkeypatch.setattr(stages, "build_context", lambda selected: ("context", []))

    def fake_generate(system_prompt, user_prompt, model=None):
        generated["model"] = model
        return "Answer [1]"

    monkeypatch.setattr(stages, "generate", fake_generate)

    response, trace_record = answer_service.run_answer(
        "Which law controls after the amendment?",
        trace=True,
    )

    assert generated["model"] == "claude-haiku-4-5"
    assert response["generator_model"] == "claude-haiku-4-5"
    assert response["model_choice"] == {
        "model": "claude-haiku-4-5",
        "reason": "intent:amendment_or_current_law",
    }
    assert trace_record == records[0]
    assert trace_record["generator_model"] == "claude-haiku-4-5"
    assert trace_record["model_choice"] == response["model_choice"]
    assert trace_record["evidence"] == {
        "verdict": "sufficient",
        "method": "min_chunks",
        "missing_facets": [],
        "detail": {
            "pre_expansion_count": 1,
            "selected_count": 1,
            "min_chunks_for_answer": 1,
        },
    }
    assert trace_record["corrective_retrieval"] == {
        "enabled": False,
        "mode": "append",
        "fired": False,
        "added_chunks": 0,
        "baseline_selected_count": 1,
        "post_selected_count": 1,
        "max_added": 2,
        "displaced_baseline_count": None,
    }


def test_answer_greeting_router_on_does_not_classify(monkeypatch):
    records = _capture_traces(monkeypatch)
    monkeypatch.setattr(settings, "router_enabled", True)

    def fail_classify(question, model=None):
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


def test_answer_greeting_with_session_still_finalizes(monkeypatch):
    records = _capture_traces(monkeypatch)
    appended = []
    monkeypatch.setattr(runner, "_ensure_session", lambda session_id: None)
    monkeypatch.setattr(runner, "_append_session_turn", lambda state: appended.append(state))

    response, trace_record = answer_service.run_answer(
        "hi",
        debug=True,
        session_id="s1",
        trace=True,
    )

    assert response["answer"]
    assert response["debug"]["stages"] == []
    assert len(appended) == 1
    assert appended[0].session_id == "s1"
    assert len(records) == 1
    assert trace_record == records[0]
    assert trace_record["session_id"] == "s1"


def test_llm_error_path_returns_error_response(monkeypatch):
    monkeypatch.setattr(settings, "trace_logging_enabled", False)
    monkeypatch.setattr(settings, "min_chunks_for_answer", 1)

    class FakeStrategy:
        def execute(self, question, knobs=None, *, legal_query=None):
            return SelectionResult(
                retrieved=[],
                pre_expansion=[RetrievalResult("c1", "context", 1.0, {})],
                selected=[RetrievalResult("c1", "context", 1.0, {})],
            )

    monkeypatch.setitem(stages.STRATEGIES, "default", FakeStrategy())
    monkeypatch.setattr(stages, "build_context", lambda selected: ("context", []))

    def fail_generate(system_prompt, user_prompt, model=None):
        raise LLMError("offline")

    monkeypatch.setattr(stages, "generate", fail_generate)

    response = answer_service.answer("What is theft?", trace=False)

    assert response["error"] is True
    assert response["abstained"] is False
    assert "offline" in response["answer"]


def test_unexpected_pipeline_exception_propagates_without_finalize(monkeypatch):
    records = _capture_traces(monkeypatch)
    appended = []
    monkeypatch.setattr(settings, "router_enabled", False)
    monkeypatch.setattr(runner, "_ensure_session", lambda session_id: None)
    monkeypatch.setattr(runner, "_append_session_turn", lambda state: appended.append(state))

    def fail_retrieve_context(state):
        raise RuntimeError("boom")

    monkeypatch.setattr(stages, "retrieve_context", fail_retrieve_context)

    with pytest.raises(RuntimeError, match="boom"):
        answer_service.answer("What is theft?", session_id="s1", trace=True)

    assert records == []
    assert appended == []


def test_hard_abstain_when_min_chunks_not_met(monkeypatch):
    records = _capture_traces(monkeypatch)
    monkeypatch.setattr(settings, "min_chunks_for_answer", 2)

    class FakeStrategy:
        def execute(self, question, knobs=None, *, legal_query=None):
            return SelectionResult(
                retrieved=[],
                pre_expansion=[RetrievalResult("c1", "context", 1.0, {})],
                selected=[RetrievalResult("c1", "context", 1.0, {})],
            )

    monkeypatch.setitem(stages.STRATEGIES, "default", FakeStrategy())

    response, trace_record = answer_service.run_answer(
        "What is a deliberately obscure corpus miss?",
        trace=True,
    )

    assert response["abstained"] is True
    assert response["error"] is False
    assert response["sources"] == []
    assert trace_record == records[0]
    assert trace_record["evidence"] == {
        "verdict": "insufficient",
        "method": "min_chunks",
        "missing_facets": [],
        "detail": {
            "pre_expansion_count": 1,
            "selected_count": 1,
            "min_chunks_for_answer": 2,
        },
    }
    assert trace_record["corrective_retrieval"] == {
        "enabled": False,
        "mode": "append",
        "fired": False,
        "added_chunks": 0,
        "baseline_selected_count": 1,
        "post_selected_count": 1,
        "max_added": 2,
        "displaced_baseline_count": None,
    }
