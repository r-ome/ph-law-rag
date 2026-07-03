import pytest

from app.config import SourceConfig
from app.source_metadata import build_source_metadata, operability_action_for


def _source(**overrides) -> SourceConfig:
	data = {
		"source_id": "civil_code",
		"title": "Civil Code",
		"official_number": "RA 386",
		"url": "https://example.test/civil-code",
		"doc_type": "republic_act",
		"file_format": "html",
		"category": "statute",
		"tags": ["civil"],
		"enabled": True,
		"status": "operative",
		"source_index": "lawphil",
		"structure": "auto",
	}
	data.update(overrides)
	return SourceConfig(**data)


@pytest.mark.parametrize("status", ["superseded", "repealed", "not_yet_effective"])
def test_non_operative_statuses_hide(status):
	assert operability_action_for(status) == "hide"


@pytest.mark.parametrize("status", ["operative", "unknown", None])
def test_operative_unknown_and_unset_statuses_show(status):
	assert operability_action_for(status) == "show"


def test_build_source_metadata_includes_required_fields():
	meta = build_source_metadata(_source(), "doc-1")

	assert meta == {
		"doc_id": "doc-1",
		"source_id": "civil_code",
		"title": "Civil Code",
		"official_number": "RA 386",
		"url": "https://example.test/civil-code",
		"doc_type": "republic_act",
		"category": "statute",
		"tags": ["civil"],
		"structure": "auto",
		"status": "operative",
		"operability_action": "show",
	}


def test_build_source_metadata_omits_empty_amendment_routing_fields():
	meta = build_source_metadata(_source(amends=[], amends_namespace=None), "doc-1")

	assert "amends" not in meta
	assert "amends_namespace" not in meta


def test_build_source_metadata_includes_non_empty_amendment_routing_fields():
	meta = build_source_metadata(
		_source(amends=["base_law"], amends_namespace="base_law"),
		"doc-1",
	)

	assert meta["amends"] == ["base_law"]
	assert meta["amends_namespace"] == "base_law"
