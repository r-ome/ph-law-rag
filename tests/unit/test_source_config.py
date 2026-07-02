import pytest
from pydantic import ValidationError

from app.config import SourceConfig

pytestmark = pytest.mark.unit


def _source(**overrides):
	data = {
		"source_id": "amendment",
		"title": "Amendment",
		"url": "https://example.test/amendment",
		"doc_type": "republic_act",
		"file_format": "html",
		"category": "statute",
		"tags": ["criminal"],
		"enabled": True,
		"status": "operative",
		"source_index": "lawphil",
	}
	data.update(overrides)
	return SourceConfig(**data)


def test_amends_namespace_must_be_non_empty():
	with pytest.raises(ValidationError):
		_source(amends=["target"], amends_namespace="")


def test_amends_namespace_must_be_in_amends():
	with pytest.raises(ValidationError):
		_source(amends=["target"], amends_namespace="other")


def test_amends_namespace_accepts_member_of_amends():
	source = _source(amends=["target"], amends_namespace="target")

	assert source.amends_namespace == "target"
