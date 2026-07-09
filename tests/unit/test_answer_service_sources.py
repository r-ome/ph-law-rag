from app.retriever.answer_service import _chunk_trace, _cited_sources
from app.retriever.types import RetrievalResult


def test_cited_sources_keeps_only_sources_referenced_by_answer() -> None:
    sources = [
        {"ref": 1, "title": "A"},
        {"ref": 2, "title": "B"},
        {"ref": 3, "title": "C"},
    ]

    assert _cited_sources("Only this source is cited [2].", sources) == [
        {"ref": 2, "title": "B"}
    ]


def test_cited_sources_returns_empty_without_citations() -> None:
    assert _cited_sources("No citation marker.", [{"ref": 1, "title": "A"}]) == []


def test_chunk_trace_serializes_consolidated_as_string() -> None:
	text = "Example text with more detail"
	trace = _chunk_trace(
		RetrievalResult(
			chunk_id="c1",
			text=text,
			score=1.0,
			metadata={"consolidated": 1},
		),
		preview_chars=12,
	)

	assert trace["consolidated"] == "1"
	assert trace["preview"] == text[:12]
	assert trace["text"] == text
