import pytest

from app.retriever.context_selection import select_context
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _r(chunk_id: str, **metadata) -> RetrievalResult:
	return RetrievalResult(chunk_id=chunk_id, text=chunk_id, score=1.0, metadata=metadata)


def test_select_context_exposes_pre_expansion_for_gates_and_selected_for_generation(monkeypatch):
	from app.config import settings

	monkeypatch.setattr(settings, "subquery_packaging_enabled", False)
	monkeypatch.setattr(settings, "edge_expansion_enabled", True)
	monkeypatch.setattr(settings, "prefer_operative_enabled", False)
	monkeypatch.setattr(settings, "parent_expansion_enabled", True)
	monkeypatch.setattr(settings, "consolidated_dedup_enabled", True)

	raw = [_r("raw")]
	reranked = [_r("pre")]
	expanded = [_r("expanded", expanded_from_parent=True)]
	deduped = [_r("selected", expanded_from_parent=True)]

	monkeypatch.setattr("app.retriever.context_selection.hybrid_retriever", lambda question, knobs=None: raw)
	monkeypatch.setattr("app.retriever.context_selection.rerank", lambda question, results, knobs=None: reranked)
	monkeypatch.setattr("app.retriever.context_selection.expand_with_edges", lambda question, results, knobs=None: results)
	monkeypatch.setattr("app.retriever.parent_expansion.expand_parents", lambda results, knobs=None: expanded)
	monkeypatch.setattr("app.retriever.dedup.dedup_results", lambda results: deduped)

	selection = select_context("question")

	assert selection.retrieved == raw
	assert selection.pre_expansion == reranked
	assert selection.selected == deduped


def test_select_context_preserves_retrieved_scores_before_in_place_rerank(monkeypatch):
	from app.config import settings

	monkeypatch.setattr(settings, "subquery_packaging_enabled", False)
	monkeypatch.setattr(settings, "edge_expansion_enabled", False)
	monkeypatch.setattr(settings, "prefer_operative_enabled", False)
	monkeypatch.setattr(settings, "parent_expansion_enabled", False)
	monkeypatch.setattr(settings, "consolidated_dedup_enabled", False)

	raw = [
		RetrievalResult(chunk_id="low", text="low", score=0.0167, metadata={"source_id": "a"}),
		RetrievalResult(chunk_id="high", text="high", score=0.0328, metadata={"source_id": "b"}),
	]

	def fake_rerank(question, results, knobs=None):
		results[0].score = 5.0
		results[1].score = 1.0
		results.sort(key=lambda r: r.score, reverse=True)
		return results[:1]

	monkeypatch.setattr("app.retriever.context_selection.hybrid_retriever", lambda question, knobs=None: raw)
	monkeypatch.setattr("app.retriever.context_selection.rerank", fake_rerank)

	selection = select_context("question")

	assert [(r.chunk_id, r.score) for r in selection.retrieved] == [
		("low", 0.0167),
		("high", 0.0328),
	]
	assert [(r.chunk_id, r.score) for r in selection.pre_expansion] == [("low", 5.0)]
