from dataclasses import replace

import pytest

from app.pipeline.evidence import evaluate_evidence
from app.pipeline.policy import AnswerPolicy
from app.pipeline.corrective import corrective_retrieve
from app.pipeline.state import AnswerState
from app.retriever.context_selection import SelectionResult
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _state(pre_expansion_count: int, selected_count: int | None = None) -> AnswerState:
    selected_count = pre_expansion_count if selected_count is None else selected_count
    pre_expansion = [
        RetrievalResult(f"p{i}", f"context {i}", 1.0, {}) for i in range(pre_expansion_count)
    ]
    selected = [
        RetrievalResult(f"s{i}", f"context {i}", 1.0, {}) for i in range(selected_count)
    ]
    state = AnswerState(question="What is theft?", debug_enabled=False)
    state.selection = SelectionResult(
        retrieved=pre_expansion,
        pre_expansion=pre_expansion,
        selected=selected,
    )
    return state


def _policy(**overrides) -> AnswerPolicy:
    return replace(AnswerPolicy.from_settings(), **overrides)


def test_min_chunks_gate_reports_insufficient():
    report = evaluate_evidence(_state(pre_expansion_count=1), _policy(min_chunks_for_answer=2))

    assert report.verdict == "insufficient"
    assert report.method == "min_chunks"
    assert report.missing_facets == []
    assert report.detail == {
        "pre_expansion_count": 1,
        "selected_count": 1,
        "min_chunks_for_answer": 2,
    }


def test_min_chunks_gate_reports_sufficient():
    report = evaluate_evidence(_state(pre_expansion_count=2), _policy(min_chunks_for_answer=2))

    assert report.verdict == "sufficient"
    assert report.method == "min_chunks"


def test_answerability_gate_reports_answerable_result(monkeypatch):
    monkeypatch.setattr("app.pipeline.evidence.is_answerable", lambda question, chunks: False)

    report = evaluate_evidence(
        _state(pre_expansion_count=2),
        _policy(evidence_gate="answerability", min_chunks_for_answer=1),
    )

    assert report.verdict == "insufficient"
    assert report.method == "answerability_gate"
    assert report.detail["answerable"] is False


def test_corrective_retrieve_noop_marks_invocation():
    state = _state(pre_expansion_count=2)

    returned = corrective_retrieve(state, _policy(corrective_retrieval_enabled=True))

    assert returned is state
    assert state.corrective_ran is True
    assert state.corrective_added_chunks == 0
