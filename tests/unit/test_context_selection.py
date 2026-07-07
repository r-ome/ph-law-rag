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
