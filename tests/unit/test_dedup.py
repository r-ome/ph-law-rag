import pytest

from app.retriever.dedup import dedup_results
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _r(chunk_id: str, text: str, score: float = 1.0, **metadata) -> RetrievalResult:
	return RetrievalResult(chunk_id=chunk_id, text=text, score=score, metadata=metadata)


def test_dedup_keeps_parent_expanded_consolidated_and_drops_amendment_duplicate():
	base = _r(
		"parent",
		"Article 309. Theft penalties as amended.",
		provision_id="revised_penal_code:article:309",
		source_id="revised_penal_code",
		consolidated=1,
		expanded_from_parent=True,
	)
	amendment = _r(
		"amendment",
		"Article 309. Theft penalties as amended.",
		provision_id="revised_penal_code:article:309",
		source_id="rpc_penalty_amendments_2017",
	)

	assert dedup_results([base, amendment]) == [base]


def test_dedup_preserves_distinct_same_provision_fragments():
	first = _r(
		"a",
		"Section 21(4). The apprehending team shall inventory the seized drugs.",
		provision_id="dangerous_drugs_act:article-ii:section:21",
	)
	second = _r(
		"b",
		"Section 21(8). The Dangerous Drugs Board shall issue implementing rules.",
		provision_id="dangerous_drugs_act:article-ii:section:21",
	)

	assert dedup_results([first, second]) == [first, second]


def test_dedup_near_identical_fallback_keeps_better_ranked_result():
	first = _r(
		"a",
		"Article 1. This is a long provision with enough repeated legal wording to compare safely.",
		provision_id="demo:article:1",
	)
	second = _r(
		"b",
		"Article 1. This is a long provision with enough repeated legal wording to compare safely.",
		provision_id="demo:article:1",
	)

	assert dedup_results([first, second]) == [first]


def test_dedup_merges_same_source_consolidated_fragments_without_losing_text():
	first = _r(
		"a",
		"Article 309. First penalty bracket.",
		provision_id="revised_penal_code:article:309",
		source_id="revised_penal_code",
		consolidated=1,
	)
	second = _r(
		"b",
		"Article 309. Later penalty bracket.",
		provision_id="revised_penal_code:article:309",
		source_id="revised_penal_code",
		consolidated=1,
	)

	out = dedup_results([first, second])

	assert len(out) == 1
	assert out[0].chunk_id == "a"
	assert "First penalty bracket" in out[0].text
	assert "Later penalty bracket" in out[0].text
	assert out[0].metadata["dedup_merged_chunk_ids"] == ["a", "b"]


def test_dedup_never_drops_parent_expanded_results_as_candidates():
	parent = _r(
		"parent",
		"Article 1. This is a long provision with enough repeated legal wording to compare safely.",
		provision_id="demo:article:1",
		expanded_from_parent=True,
	)
	duplicate = _r(
		"duplicate",
		"Article 1. This is a long provision with enough repeated legal wording to compare safely.",
		provision_id="demo:article:1",
	)

	assert dedup_results([duplicate, parent]) == [duplicate, parent]


def test_dedup_preserves_consolidated_sibling_leaf_identity():
	seed = _r(
		"seed",
		"Article 1403(2)(d). Credit agreement.",
		provision_id="civil_code:article:1403",
		source_id="civil_code",
		consolidated=1,
	)
	sibling = _r(
		"sibling",
		"Article 1403(2)(e). Sale of real property.",
		provision_id="civil_code:article:1403",
		source_id="civil_code",
		consolidated=1,
		expanded_from_sibling=True,
		unit_label="Article 1403(2)(e)",
	)

	assert dedup_results([seed, sibling]) == [seed, sibling]


def test_sibling_addition_cannot_evict_similar_original_seed():
	text = "Article 1. This is a long provision with enough repeated legal wording to compare safely."
	sibling = _r(
		"sibling",
		text,
		provision_id="demo:article:1",
		expanded_from_sibling=True,
	)
	seed = _r("seed", text, provision_id="demo:article:1")

	assert dedup_results([sibling, seed]) == [sibling, seed]


def test_consolidated_sibling_cannot_evict_nonconsolidated_seed():
	text = "Article 1. This is a long provision with enough repeated legal wording to compare safely."
	sibling = _r(
		"sibling",
		text,
		provision_id="demo:article:1",
		source_id="demo",
		consolidated=1,
		expanded_from_sibling=True,
	)
	seed = _r(
		"seed",
		text,
		provision_id="demo:article:1",
		source_id="demo",
	)

	assert dedup_results([sibling, seed]) == [sibling, seed]
