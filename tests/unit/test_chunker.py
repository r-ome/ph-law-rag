import pytest

from app.indexing.chunker import (
	_detect_units,
	_looks_structural,
	_number_key,
	chunk_texts,
	extract_parents,
)

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


def test_amendment_quoted_article_uses_target_namespace():
	text = 'Section 1. Article 266-A is inserted.\n"Article 266-A. Rape: When and how committed.\nText.'
	sm = {**SOURCE_METADATA, "source_id": "anti_rape_law_1997", "amends": ["revised_penal_code"], "structure": "prose"}

	nodes = chunk_texts(text, sm)
	inserted = [n for n in nodes if n.metadata.get("inserted_into")]

	assert len(inserted) == 1
	assert inserted[0].metadata["unit_label"] == "Article 266-A"
	assert inserted[0].metadata["provision_id"] == "revised_penal_code:article:266-a"
	assert inserted[0].metadata["inserted_into"] == "revised_penal_code"
	assert inserted[0].metadata["source_id"] == "anti_rape_law_1997"


def test_quoted_article_without_amends_remains_prose():
	text = '"Article 266-A. Rape: When and how committed.\nText quoted in a decision.'

	nodes = chunk_texts(text, {**SOURCE_METADATA, "structure": "hierarchical"})

	assert all(not n.metadata.get("is_structural") for n in nodes)
	assert all("unit_label" not in n.metadata for n in nodes)


def test_amendment_multi_target_namespace_resolution(capsys):
	text = '"Section 21. Chain of custody.\nText.'
	without_namespace = {
		**SOURCE_METADATA,
		"source_id": "multi_amendment",
		"amends": ["dangerous_drugs_act", "other_act"],
	}
	with_namespace = {**without_namespace, "amends_namespace": "dangerous_drugs_act"}

	fallback_nodes = chunk_texts(text, without_namespace)
	out = capsys.readouterr().out
	namespace_nodes = chunk_texts(text, with_namespace)

	assert "multi-target amendment without amends_namespace" in out
	assert fallback_nodes[0].metadata["provision_id"] == "multi_amendment:section:21"
	assert namespace_nodes[0].metadata["provision_id"] == "dangerous_drugs_act:section:21"


def test_amendment_partial_ellipsis_metadata_only_when_present():
	base = {**SOURCE_METADATA, "source_id": "amendment", "amends": ["target_act"]}

	partial = chunk_texts('"Article 10. Existing text.\nx x x\nNew text.', base)[0]
	complete = chunk_texts('"Article 11. Complete replacement text.', base)[0]

	assert partial.metadata["provision_partial"] is True
	assert "provision_partial" not in complete.metadata


def test_suffix_number_key_keeps_structural_run_ascending():
	units = [
		{"number": "335"},
		{"number": "335-A"},
		{"number": "336"},
		{"number": "337"},
		{"number": "338"},
	]

	assert _number_key("335-A") > _number_key("335")
	assert _looks_structural(units)


def test_suffix_regex_migration_preserves_unit_spans():
	# _detect_units is private; this is a one-time migration guard, deletable after
	# verification — not a contract on internals.
	text = (
		"Article 208. First.\n"
		"Body.\n"
		"Article 208-A. Inserted.\n"
		"Body.\n"
		"Article 209. Next.\n"
	)
	units = _detect_units(text)

	assert [u["start"] for u in units] == [
		text.index("Article 208."),
		text.index("Article 208-A."),
		text.index("Article 209."),
	]
	assert [u["number"] for u in units] == ["208", "208-A", "209"]
	assert [u["label"] for u in units] == ["Article 208", "Article 208-A", "Article 209"]


def test_oversized_inserted_unit_leaves_inherit_target_provision_and_parent(monkeypatch):
	monkeypatch.setattr("app.indexing.chunker.settings.chunk_size", 30)
	monkeypatch.setattr("app.indexing.chunker.settings.chunk_overlap", 5)
	text = (
		'"Article 266-A. Rape: When and how committed.\n'
		"Opening text long enough to force enumeration splitting in this synthetic law.\n"
		"(a) First mode text.\n"
		"(b) Second mode text.\n"
	)
	sm = {**SOURCE_METADATA, "source_id": "anti_rape_law_1997", "amends": ["revised_penal_code"]}

	nodes = chunk_texts(text, sm)
	parents = extract_parents(text, sm)
	leaves = [n for n in nodes if n.metadata.get("parent_key")]

	assert leaves
	assert all(n.metadata["provision_id"] == "revised_penal_code:article:266-a" for n in leaves)
	assert all(n.metadata["inserted_into"] == "revised_penal_code" for n in leaves)
	assert parents
	assert parents[0]["parent_key"] == leaves[0].metadata["parent_key"]


def test_quote_stripped_out_of_sequence_sections_are_inserted():
	# lawphil strips quotation marks on some amendment pages (RA 11576, RA 10707); the
	# surviving insertion signal is sequence: own sections run 1, 2, 3, … while a
	# quote-stripped insertion breaks the series.
	text = (
		"Section 1. Section 19 of Batas Pambansa Blg. 129 is hereby amended to read as follows:\n"
		"Section 19. Jurisdiction of the Regional Trial Courts. Text.\n"
		"Section 2. Section 33 of the same law is hereby amended to read as follows:\n"
		"Section 33. Jurisdiction of the Metropolitan Trial Courts. Text.\n"
		"Section 3. Effectivity. This Act shall take effect.\n"
	)
	sm = {
		**SOURCE_METADATA,
		"source_id": "judiciary_reorganization_amendments_2021",
		"amends": ["judiciary_reorganization_act"],
	}

	nodes = chunk_texts(text, sm)
	by_label = {n.metadata["unit_label"]: n.metadata for n in nodes}

	assert by_label["Section 1"].get("inserted_into") is None
	assert by_label["Section 1"]["provision_id"] == "judiciary_reorganization_amendments_2021:section:1"
	assert by_label["Section 19"]["inserted_into"] == "judiciary_reorganization_act"
	assert by_label["Section 19"]["provision_id"] == "judiciary_reorganization_act:section:19"
	assert by_label["Section 33"]["inserted_into"] == "judiciary_reorganization_act"
	assert by_label["Section 2"].get("inserted_into") is None
	assert by_label["Section 3"].get("inserted_into") is None


def test_unquoted_article_in_amendment_mode_is_inserted():
	# An RA/PD amendment's own units are Sections, never Articles — a bare Article marker
	# is an insertion whose quotes were stripped in extraction.
	text = (
		"Section 1. Article 100 of the Revised Penal Code is hereby amended to read as follows:\n"
		"Article 100. Civil liability of a person guilty of felony. Text.\n"
	)
	sm = {**SOURCE_METADATA, "source_id": "some_amendment", "amends": ["revised_penal_code"]}

	nodes = chunk_texts(text, sm)
	article = [n for n in nodes if n.metadata["unit_label"] == "Article 100"][0]

	assert article.metadata["inserted_into"] == "revised_penal_code"
	assert article.metadata["provision_id"] == "revised_penal_code:article:100"
