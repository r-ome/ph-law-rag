import pytest

from app.indexing.chunker import _provision_id, chunk_texts
from app.indexing.vector_store import operability_action_for, operative_filter
from app.indexing.provision_status import ProvisionOverride, apply_overrides
from app.retriever.prefer_operative import prefer_operative
from app.retriever.supersession import SupersessionRule, provision_matches
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


# ── provision_id derivation ────────────────────────────────────────────────
def test_provision_id_article_uses_source_prefix():
	assert _provision_id("revised_penal_code", "article", "335", "") == "revised_penal_code:article:335"


def test_provision_id_section_folds_in_path():
	# section numbers reset per parent, so the structure_path disambiguates
	assert _provision_id("constitution_1987", "section", "1", "ARTICLE III") == \
		"constitution_1987:article-iii:section:1"


def test_provision_id_none_without_source():
	assert _provision_id(None, "article", "1", "") is None


def test_structural_chunks_carry_provision_id_prose_does_not():
	text = (
		"AN ACT defining things.\n"
		"Article 1. The first rule states a principle of general application.\n"
		"Article 2. The second rule states another principle of general application.\n"
		"Article 3. The third rule states yet another principle of general application.\n"
		"Article 4. The fourth rule states a further principle of general application.\n"
		"Article 5. The fifth rule states a final principle of general application.\n"
	)
	sm = {"source_id": "demo_act", "title": "Demo", "structure": "hierarchical"}
	nodes = chunk_texts(text, sm)
	structural = [n for n in nodes if n.metadata.get("is_structural")]
	prose = [n for n in nodes if not n.metadata.get("is_structural")]
	assert {n.metadata["provision_id"] for n in structural} >= {"demo_act:article:1", "demo_act:article:5"}
	assert all("provision_id" not in n.metadata for n in prose)


# ── operability_action default ─────────────────────────────────────────────
@pytest.mark.parametrize("status,expected", [
	("operative", "show"),
	("unknown", "show"),
	("superseded", "hide"),
	("repealed", "hide"),
	("not_yet_effective", "hide"),
])
def test_operability_action_for(status, expected):
	assert operability_action_for(status) == expected


# ── apply_overrides ────────────────────────────────────────────────────────
def _overrides():
	return {"revised_penal_code:article:335": ProvisionOverride(
		provision_id="revised_penal_code:article:335",
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="anti_rape_law_1997",
		effective_date="1997-10-22",
		note="reclassified",
	)}


def test_apply_overrides_stamps_without_touching_status():
	meta = {"provision_id": "revised_penal_code:article:335", "status": "operative", "operability_action": "show"}
	apply_overrides(meta, _overrides())
	assert meta["status"] == "operative"            # document status untouched
	assert meta["provision_status"] == "superseded"
	assert meta["operability_action"] == "hide"
	assert meta["operability_basis_source_id"] == "anti_rape_law_1997"


def test_apply_overrides_no_match_is_noop():
	meta = {"provision_id": "civil_code:article:19", "status": "operative", "operability_action": "show"}
	apply_overrides(meta, _overrides())
	assert meta == {"provision_id": "civil_code:article:19", "status": "operative", "operability_action": "show"}


def test_apply_overrides_skips_chunks_without_provision_id():
	meta = {"is_structural": False, "operability_action": "show"}
	apply_overrides(meta, _overrides())
	assert "provision_status" not in meta


# ── retrieval filter repoint ───────────────────────────────────────────────
def test_operative_filter_excludes_hide(monkeypatch):
	from app.config import settings
	monkeypatch.setattr(settings, "retrieval_operative_only", True)
	f = operative_filter()
	cond = f.must_not[0]
	assert cond.key == "operability_action"
	assert cond.match.value == "hide"


def test_operative_filter_off_returns_none(monkeypatch):
	from app.config import settings
	monkeypatch.setattr(settings, "retrieval_operative_only", False)
	assert operative_filter() is None


def test_provision_matches_exact_provision_id_only():
	assert provision_matches("dangerous_drugs_act:section:21", ["dangerous_drugs_act:section:21"])
	assert not provision_matches("dangerous_drugs_act:section:21(a)", ["dangerous_drugs_act:section:21"])


def test_prefer_operative_demotes_base_same_provision_id_different_source(monkeypatch):
	from app.config import settings
	monkeypatch.setattr(settings, "prefer_operative_enabled", True)
	rule = SupersessionRule(
		base_source_id="dangerous_drugs_act",
		base_provision_ids=("dangerous_drugs_act:section:21",),
		operative_source_id="dangerous_drugs_amendments_2014",
		operative_provision_ids=("dangerous_drugs_act:section:21",),
		kind="amendment",
	)
	monkeypatch.setattr("app.retriever.prefer_operative.load_supersessions", lambda: (rule,))
	base = RetrievalResult(
		chunk_id="base",
		text="old",
		score=1.0,
		metadata={"source_id": "dangerous_drugs_act", "provision_id": "dangerous_drugs_act:section:21"},
	)
	operative = RetrievalResult(
		chunk_id="operative",
		text="new",
		score=0.9,
		metadata={"source_id": "dangerous_drugs_amendments_2014", "provision_id": "dangerous_drugs_act:section:21"},
	)
	other = RetrievalResult(
		chunk_id="other",
		text="other",
		score=0.8,
		metadata={"source_id": "other", "provision_id": "other:section:1"},
	)

	assert [r.chunk_id for r in prefer_operative([base, operative, other])] == ["operative", "other", "base"]


def test_prefer_operative_noop_when_operative_absent(monkeypatch):
	from app.config import settings
	monkeypatch.setattr(settings, "prefer_operative_enabled", True)
	rule = SupersessionRule(
		base_source_id="dangerous_drugs_act",
		base_provision_ids=("dangerous_drugs_act:section:21",),
		operative_source_id="dangerous_drugs_amendments_2014",
		operative_provision_ids=("dangerous_drugs_act:section:21",),
		kind="amendment",
	)
	monkeypatch.setattr("app.retriever.prefer_operative.load_supersessions", lambda: (rule,))
	base = RetrievalResult(
		chunk_id="base",
		text="old",
		score=1.0,
		metadata={"source_id": "dangerous_drugs_act", "provision_id": "dangerous_drugs_act:section:21"},
	)

	assert prefer_operative([base]) == [base]
