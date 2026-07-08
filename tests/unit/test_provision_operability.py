import pytest

from app.indexing.chunker import _provision_id, chunk_texts
from app.indexing.vector_store import operability_action_for, operative_filter
from app.indexing.provision_status import ProvisionOverride, apply_overrides, load_provision_overrides
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
	return {"revised_penal_code:article:335": (ProvisionOverride(
		provision_id="revised_penal_code:article:335",
		source_id=None,
		unit_labels=None,
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="anti_rape_law_1997",
		effective_date="1997-10-22",
		note="reclassified",
	),)}


def _section_21_overrides(labels=("Section 21", "Section 21(1)", "Section 21(2)", "Section 21(3)")):
	return {"dangerous_drugs_act:article-ii:section:21": (ProvisionOverride(
		provision_id="dangerous_drugs_act:article-ii:section:21",
		source_id="dangerous_drugs_act",
		unit_labels=tuple(labels),
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="dangerous_drugs_amendments_2014",
		effective_date="2014-07-15",
		note="partial restatement",
	),)}


def _anti_hazing_section_2_override():
	return {"anti_hazing:section:2": (ProvisionOverride(
		provision_id="anti_hazing:section:2",
		source_id="anti_hazing",
		unit_labels=None,
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="anti_hazing_amendments_2018",
		effective_date="2018-06-29",
		note="RA 11053 bans hazing outright.",
	),)}


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


def test_apply_overrides_leaf_match_hides_exact_unit_label():
	meta = {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_act",
		"unit_label": "Section 21(1)",
	}
	apply_overrides(meta, _section_21_overrides())
	assert meta["provision_status"] == "superseded"
	assert meta["operability_action"] == "hide"
	assert meta["operability_basis_source_id"] == "dangerous_drugs_amendments_2014"
	assert "parent_has_hidden_leaves" not in meta


def test_apply_overrides_leaf_sibling_is_flagged_but_not_hidden():
	meta = {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_act",
		"unit_label": "Section 21(4)",
	}
	apply_overrides(meta, _section_21_overrides())
	assert meta["parent_has_hidden_leaves"] == 1
	assert "operability_action" not in meta


def test_apply_overrides_leaf_labels_are_exact_not_prefix_matches():
	item = {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_act",
		"unit_label": "Section 21(1)",
	}
	apply_overrides(item, _section_21_overrides(labels=("Section 21",)))
	assert "operability_action" not in item
	assert item["parent_has_hidden_leaves"] == 1

	chapeau = {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_act",
		"unit_label": "Section 21",
	}
	apply_overrides(chapeau, _section_21_overrides(labels=("Section 21(1)",)))
	assert "operability_action" not in chapeau
	assert chapeau["parent_has_hidden_leaves"] == 1


def test_apply_overrides_source_id_mismatch_is_complete_noop():
	meta = {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_amendments_2014",
		"unit_label": "Section 21(1)",
	}
	apply_overrides(meta, _section_21_overrides())
	assert meta == {
		"provision_id": "dangerous_drugs_act:article-ii:section:21",
		"source_id": "dangerous_drugs_amendments_2014",
		"unit_label": "Section 21(1)",
	}


def test_apply_overrides_same_id_collision_hide_is_source_scoped():
	base = {
		"provision_id": "anti_hazing:section:2",
		"source_id": "anti_hazing",
		"status": "operative",
		"operability_action": "show",
	}
	operative = {
		"provision_id": "anti_hazing:section:2",
		"source_id": "anti_hazing_amendments_2018",
		"status": "operative",
		"operability_action": "show",
	}
	apply_overrides(base, _anti_hazing_section_2_override())
	apply_overrides(operative, _anti_hazing_section_2_override())

	assert base["provision_status"] == "superseded"
	assert base["operability_action"] == "hide"
	assert base["operability_basis_source_id"] == "anti_hazing_amendments_2018"
	assert "provision_status" not in operative
	assert operative["operability_action"] == "show"


def test_apply_overrides_legacy_whole_provision_hides_without_flag():
	meta = {"provision_id": "revised_penal_code:article:335", "unit_label": "Article 335"}
	apply_overrides(meta, _overrides())
	assert meta["operability_action"] == "hide"
	assert "parent_has_hidden_leaves" not in meta


def test_apply_overrides_hide_beats_prior_leaf_survivor_flag():
	pid = "dangerous_drugs_act:article-ii:section:21"
	overrides = {pid: (
		ProvisionOverride(
			provision_id=pid,
			source_id="dangerous_drugs_act",
			unit_labels=("Section 21(1)",),
			provision_status="superseded",
			operability_action="hide",
			basis_source_id="dangerous_drugs_amendments_2014",
			effective_date="2014-07-15",
			note=None,
		),
		ProvisionOverride(
			provision_id=pid,
			source_id="dangerous_drugs_act",
			unit_labels=("Section 21(4)",),
			provision_status="superseded",
			operability_action="hide",
			basis_source_id="dangerous_drugs_amendments_2014",
			effective_date="2014-07-15",
			note=None,
		),
	)}
	meta = {"provision_id": pid, "source_id": "dangerous_drugs_act", "unit_label": "Section 21(4)"}
	apply_overrides(meta, overrides)
	assert meta["operability_action"] == "hide"
	assert "parent_has_hidden_leaves" not in meta


def test_apply_overrides_synthetic_section_21_chunk_set_and_amendment_pid_sanity():
	labels = ["Section 21"] + [f"Section 21({i})" for i in range(1, 9)]
	chunks = [
		{
			"provision_id": "dangerous_drugs_act:article-ii:section:21",
			"source_id": "dangerous_drugs_act",
			"unit_label": label,
		}
		for label in labels
	]
	for chunk in chunks:
		apply_overrides(chunk, _section_21_overrides())

	hidden = [c for c in chunks if c.get("operability_action") == "hide"]
	flagged = [c for c in chunks if c.get("parent_has_hidden_leaves") == 1]
	assert [c["unit_label"] for c in hidden] == ["Section 21", "Section 21(1)", "Section 21(2)", "Section 21(3)"]
	assert [c["unit_label"] for c in flagged] == ["Section 21(4)", "Section 21(5)", "Section 21(6)", "Section 21(7)", "Section 21(8)"]

	amendment = {
		"provision_id": "dangerous_drugs_act:section:21",
		"source_id": "dangerous_drugs_amendments_2014",
		"unit_label": "Section 21",
	}
	apply_overrides(amendment, _section_21_overrides())
	assert amendment == {
		"provision_id": "dangerous_drugs_act:section:21",
		"source_id": "dangerous_drugs_amendments_2014",
		"unit_label": "Section 21",
	}


def test_generated_civil_family_overrides_expand_from_config(monkeypatch, tmp_path):
	path = tmp_path / "provision_status.yaml"
	path.write_text("""
generated_overrides:
  - source_id: civil_code
    provision_status: repealed
    operability_action: hide
    basis_source_id: family_code
    effective_date: "1988-08-03"
    note: "Family Code Art. 254 repeal clause"
    provision_ids:
      - civil_code:article:52
      - civil_code:article:53
""")
	from app.config import settings

	monkeypatch.setattr(settings, "provision_status_path", str(path))
	load_provision_overrides.cache_clear()
	try:
		overrides = load_provision_overrides()
	finally:
		load_provision_overrides.cache_clear()

	rule = overrides["civil_code:article:52"][0]
	assert rule.source_id == "civil_code"
	assert rule.provision_id == "civil_code:article:52"
	assert rule.provision_status == "repealed"
	assert rule.operability_action == "hide"
	assert rule.basis_source_id == "family_code"
	assert rule.effective_date == "1988-08-03"


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
