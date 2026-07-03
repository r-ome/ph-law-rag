import pytest

from app.retriever.parent_expansion import expand_parents
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _result(chunk_id: str, parent_key: str, flagged: bool = False) -> RetrievalResult:
	metadata = {
		"parent_key": parent_key,
		"doc_id": "doc",
		"source_id": "source",
		"title": "Title",
		"url": "https://example.test",
	}
	if flagged:
		metadata["parent_has_hidden_leaves"] = 1
	return RetrievalResult(chunk_id=chunk_id, text=f"text {chunk_id}", score=1.0, metadata=metadata)


def test_parent_expansion_skips_flagged_children_but_expands_unflagged(monkeypatch):
	from app.config import settings

	monkeypatch.setattr(settings, "parent_expansion_enabled", True)
	monkeypatch.setattr(settings, "parent_expansion_min_children", 2)
	monkeypatch.setattr(settings, "parent_expansion_max_chars", 1000)

	def fake_load_parents(keys: set[str]) -> dict[str, dict]:
		return {
			key: {
				"parent_key": key,
				"doc_id": "doc",
				"source_id": "source",
				"title": "Title",
				"url": "https://example.test",
				"unit_type": "section",
				"unit_label": "Section 1",
				"structure_path": "",
				"text": f"parent text {key}",
				"char_count": 20,
			}
			for key in keys
		}

	monkeypatch.setattr("app.retriever.parent_expansion._load_parents", fake_load_parents)
	results = [
		_result("flagged-1", "flagged-parent", flagged=True),
		_result("flagged-2", "flagged-parent", flagged=True),
		_result("plain-1", "plain-parent"),
		_result("plain-2", "plain-parent"),
	]

	expanded = expand_parents(results)

	assert [r.chunk_id for r in expanded] == ["flagged-1", "flagged-2", "plain-parent"]
	assert expanded[0].metadata["parent_has_hidden_leaves"] == 1
	assert expanded[1].metadata["parent_has_hidden_leaves"] == 1
	assert expanded[2].metadata["expanded_from_parent"] is True


def test_parent_expansion_preserves_consolidation_metadata(monkeypatch):
	from app.config import settings

	monkeypatch.setattr(settings, "parent_expansion_enabled", True)
	monkeypatch.setattr(settings, "parent_expansion_min_children", 2)
	monkeypatch.setattr(settings, "parent_expansion_max_chars", 1000)

	def fake_load_parents(keys: set[str]) -> dict[str, dict]:
		return {
			"parent": {
				"parent_key": "parent",
				"doc_id": "doc",
				"source_id": "source",
				"title": "Title",
				"url": "https://example.test",
				"unit_type": "article",
				"unit_label": "Article 309",
				"structure_path": "",
				"text": "parent text",
				"char_count": 20,
			}
		}

	monkeypatch.setattr("app.retriever.parent_expansion._load_parents", fake_load_parents)
	first = _result("c1", "parent")
	first.metadata.update({
		"provision_id": "revised_penal_code:article:309",
		"consolidated": 1,
		"consolidation_basis": "single_full_restatement",
	})
	second = _result("c2", "parent")

	expanded = expand_parents([first, second])

	assert [r.chunk_id for r in expanded] == ["parent"]
	assert expanded[0].metadata["consolidated"] == 1
	assert expanded[0].metadata["consolidation_basis"] == "single_full_restatement"
