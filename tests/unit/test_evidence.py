from dataclasses import replace

import pytest

from app.pipeline.evidence import evaluate_evidence
from app.pipeline.policy import AnswerPolicy
from app.pipeline.corrective import corrective_retrieve
from app.pipeline.state import AnswerState, EvidenceReport
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs
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


def test_crag_gate_keeps_min_chunks_floor():
    report = evaluate_evidence(
        _state(pre_expansion_count=1),
        _policy(evidence_gate="crag", min_chunks_for_answer=2),
    )

    assert report.verdict == "insufficient"
    assert report.method == "min_chunks"


def test_crag_gate_parses_missing_facets_with_crag_token_budget(monkeypatch):
    def fake_complete(system, user, model, *, max_tokens):
        assert model == "claude-haiku-4-5"
        assert max_tokens == 512
        return "\n".join(
            [
                "FACETS: penalty range; qualifying circumstance",
                "PRESENT: penalty range",
                "MISSING: qualifying circumstance",
                "VERDICT: partial",
            ]
        )

    monkeypatch.setattr("app.pipeline.evidence._gate_complete", fake_complete)

    report = evaluate_evidence(
        _state(pre_expansion_count=2),
        _policy(
            evidence_gate="crag",
            evidence_judge_model="claude-haiku-4-5",
            min_chunks_for_answer=1,
        ),
    )

    assert report.verdict == "partial"
    assert report.method == "crag_facets"
    assert report.missing_facets == ["qualifying circumstance"]
    assert report.detail["facets"] == ["penalty range", "qualifying circumstance"]
    assert report.detail["present"] == ["penalty range"]
    assert report.detail["missing"] == ["qualifying circumstance"]


def test_crag_gate_fails_open_on_malformed_output(monkeypatch):
    monkeypatch.setattr("app.pipeline.evidence._gate_complete", lambda *args, **kwargs: "bad")

    report = evaluate_evidence(
        _state(pre_expansion_count=2),
        _policy(evidence_gate="crag", min_chunks_for_answer=1),
    )

    assert report.verdict == "sufficient"
    assert report.method == "crag_facets"
    assert report.missing_facets == []


def test_crag_gate_treats_null_missing_sentinel_as_sufficient(monkeypatch):
    def fake_complete(system, user, model, *, max_tokens):
        return "\n".join(
            [
                "FACETS: penalty range",
                "PRESENT: penalty range",
                "MISSING: None",
                "VERDICT: partial",
            ]
        )

    monkeypatch.setattr("app.pipeline.evidence._gate_complete", fake_complete)

    report = evaluate_evidence(
        _state(pre_expansion_count=2),
        _policy(evidence_gate="crag", min_chunks_for_answer=1),
    )

    # "MISSING: None" means nothing is missing -> sufficient, and no corrective run.
    assert report.verdict == "sufficient"
    assert report.missing_facets == []


def test_corrective_retrieve_empty_missing_facets_is_noop():
    state = _state(pre_expansion_count=2)
    state.evidence = evaluate_evidence(state, _policy(min_chunks_for_answer=1))

    returned = corrective_retrieve(state, _policy(corrective_retrieval_enabled=True))

    assert returned is state
    assert state.corrective_ran is False
    assert state.corrective_added_chunks == 0
    assert state.corrective_baseline_selected_count == 2
    assert state.corrective_post_selected_count == 2
    assert state.corrective_max_added == 2


def test_corrective_retrieve_adds_bounded_deduped_chunks(monkeypatch):
    state = _state(pre_expansion_count=2)
    state.evidence = EvidenceReport(
        verdict="partial",
        method="crag_facets",
        missing_facets=["penalty", "elements"],
        detail={},
    )
    state.strategy_knobs = RetrievalKnobs(
        dense_top_k=10,
        sparse_top_k=10,
        rerank_top_n=8,
        parent_expansion_enabled=True,
        prefer_operative_enabled=False,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=True,
        subquery_reserve_n=2,
    )

    calls = []

    def fake_hybrid(query, knobs=None):
        calls.append(query)
        return [RetrievalResult(f"raw-{len(calls)}", query, 1.0, {})]

    def fake_rerank(query, retrieved, knobs=None):
        if "penalty" in query:
            return [
                RetrievalResult("add-1", "new penalty", 2.0, {}),
                RetrievalResult("s0", "already selected", 1.0, {}),
            ]
        if "elements" in query:
            return [
                RetrievalResult("add-2", "new elements", 2.0, {}),
                RetrievalResult("add-3", "over budget", 1.0, {}),
            ]
        # re-score against the original question: keep candidates as-is (all relevant)
        return sorted(retrieved, key=lambda r: r.score, reverse=True)

    monkeypatch.setattr("app.pipeline.corrective.hybrid_retriever", fake_hybrid)
    monkeypatch.setattr("app.pipeline.corrective.rerank", fake_rerank)

    corrective_retrieve(state, _policy(corrective_retrieval_enabled=True))

    assert calls == [
        "penalty - What is theft?",
        "elements - What is theft?",
    ]
    assert [result.chunk_id for result in state.selection.selected] == [
        "s0",
        "s1",
        "add-1",
        "add-2",
    ]
    assert state.corrective_ran is True
    assert state.corrective_added_chunks == 2
    assert state.corrective_baseline_selected_count == 2
    assert state.corrective_post_selected_count == 4
    assert state.corrective_max_added == 2


def test_corrective_drops_additions_irrelevant_to_question(monkeypatch):
    state = _state(pre_expansion_count=2)
    state.evidence = EvidenceReport(
        verdict="partial", method="crag_facets", missing_facets=["topic"], detail={}
    )
    state.strategy_knobs = RetrievalKnobs(
        dense_top_k=10,
        sparse_top_k=10,
        rerank_top_n=8,
        rerank_score_margin=0.5,
        parent_expansion_enabled=True,
        prefer_operative_enabled=False,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=True,
        subquery_reserve_n=2,
    )

    def fake_hybrid(query, knobs=None):
        return [RetrievalResult("raw", query, 1.0, {})]

    def fake_rerank(query, retrieved, knobs=None):
        if "topic" in query:  # facet query: both look good against the facet phrase
            return [
                RetrievalResult("keep", "on point", 5.0, {}),
                RetrievalResult("drop", "tangential", 4.0, {}),
            ]
        # re-score against the question: the tangential chunk falls below the margin
        return [
            RetrievalResult("keep", "on point", 5.0, {}),
            RetrievalResult("drop", "tangential", 1.0, {}),
        ]

    monkeypatch.setattr("app.pipeline.corrective.hybrid_retriever", fake_hybrid)
    monkeypatch.setattr("app.pipeline.corrective.rerank", fake_rerank)

    corrective_retrieve(state, _policy(corrective_retrieval_enabled=True))

    # "drop" (score 1.0 < 5.0 - 0.5) is filtered; only the question-relevant chunk stays.
    assert [r.chunk_id for r in state.selection.selected] == ["s0", "s1", "keep"]
    assert state.corrective_added_chunks == 1
