import pytest

from app.indexing.chunker import chunk_texts

pytestmark = pytest.mark.unit


SOURCE_METADATA = {
	"doc_id": "civil_code",
	"source_id": "civil_code",
	"title": "Civil Code of the Philippines",
	"url": "https://example.test/civil-code",
	"doc_type": "statute",
	"category": "civil",
	"tags": ["obligations", "contracts"],
}


def _long_text() -> str:
	# Long enough to force the SentenceSplitter into multiple chunks.
	sentence = (
		"Obligations arising from contracts have the force of law between "
		"the contracting parties and should be complied with in good faith. "
	)
	return sentence * 50


def test_returns_a_list_of_nodes():
	nodes = chunk_texts("A short clause about obligations.", SOURCE_METADATA)
	assert isinstance(nodes, list)
	assert len(nodes) >= 1


def test_long_text_produces_multiple_chunks():
	nodes = chunk_texts(_long_text(), SOURCE_METADATA)
	assert len(nodes) > 1


def test_metadata_propagates_to_every_node():
	nodes = chunk_texts(_long_text(), SOURCE_METADATA)
	for node in nodes:
		for key, value in SOURCE_METADATA.items():
			assert node.metadata.get(key) == value


def test_node_text_is_non_empty():
	nodes = chunk_texts(_long_text(), SOURCE_METADATA)
	assert all(node.text.strip() for node in nodes)


def test_DIVERGENCE_empty_text_yields_one_empty_node():
	# Current behavior: SentenceSplitter emits a single empty-text node for
	# empty input rather than an empty list. Downstream this would index an
	# empty chunk — worth filtering in chunker.py or index_service.py.
	nodes = chunk_texts("", SOURCE_METADATA)
	assert len(nodes) == 1
	assert nodes[0].text == ""
