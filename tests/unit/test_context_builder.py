from app.retriever.context_builder import build_context
from app.retriever.types import RetrievalResult


def _result(chunk_id: str, text: str, **metadata) -> RetrievalResult:
    base_metadata = {
        "source_id": "constitution_1987",
        "title": "1987 Constitution",
        "url": "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/3/353",
        "is_structural": True,
        "unit_type": "section",
        "unit_label": "SECTION 11",
        "structure_path": "ARTICLE VII",
    }
    base_metadata.update(metadata)
    return RetrievalResult(chunk_id=chunk_id, text=text, score=1.0, metadata=base_metadata)


def test_build_context_deduplicates_sources_by_locator() -> None:
    context, sources = build_context(
        [
            _result("c1", "First passage."),
            _result("c2", "Second passage from the same section."),
            _result("c3", "Different section.", unit_label="SECTION 12"),
        ]
    )

    assert len(sources) == 2
    assert sources[0]["ref"] == 1
    assert sources[0]["locator"] == "Article VII, Section 11"
    assert sources[1]["ref"] == 2
    assert sources[1]["locator"] == "Article VII, Section 12"
    assert context.count("[1] 1987 Constitution, Article VII, Section 11") == 2
    assert "[2] 1987 Constitution, Article VII, Section 12" in context
