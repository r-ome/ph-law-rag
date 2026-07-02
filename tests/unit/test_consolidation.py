import json
import sqlite3

import pytest

from app.config import SourceConfig, settings
from app.indexing.chunker import provision_spans
from app.indexing.consolidation import (
	Splice,
	SplicePlan,
	build_splice_plan,
	check_consolidation_coherence,
	consolidate,
)
from app.indexing.index_service import index_document

pytestmark = pytest.mark.unit


def _source(
	source_id: str,
	approval_date: str = "2000-01-01",
	amends: list[str] | None = None,
) -> SourceConfig:
	return SourceConfig(
		source_id=source_id,
		enabled=True,
		file_format="html",
		url=f"https://example.test/{source_id}",
		category="statute",
		doc_type="statute",
		tags=[],
		title=source_id,
		official_number=f"Official {source_id}",
		approval_date=approval_date,
		status="operative",
		source_index="lawphil",
		structure="hierarchical",
		amends=amends or [],
	)


def _conn() -> sqlite3.Connection:
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.executescript(
		"""
		CREATE TABLE documents(source_id TEXT, doc_id TEXT);
		CREATE TABLE document_versions(
			version_id TEXT,
			doc_id TEXT,
			fetched_at TEXT,
			normalized_path TEXT
		);
		CREATE TABLE chunks(
			chunk_id TEXT,
			doc_id TEXT,
			version_id TEXT,
			chunk_index INTEGER,
			text TEXT,
			char_count INTEGER,
			token_estimate INTEGER,
			qdrant_id TEXT,
			metadata_json TEXT,
			created_at TEXT
		);
		CREATE TABLE chunk_parents(
			parent_key TEXT,
			doc_id TEXT,
			source_id TEXT,
			title TEXT,
			url TEXT,
			unit_type TEXT,
			unit_label TEXT,
			structure_path TEXT,
			text TEXT,
			char_count INTEGER,
			created_at TEXT
		);
		"""
	)
	return conn


def _version(conn: sqlite3.Connection, source_id: str, path) -> None:
	conn.execute("INSERT INTO documents(source_id, doc_id) VALUES (?,?)", [source_id, source_id])
	conn.execute(
		"""
		INSERT INTO document_versions(version_id, doc_id, fetched_at, normalized_path)
		VALUES (?,?,?,?)
		""",
		[f"{source_id}-v1", source_id, "2026-01-01T00:00:00", str(path)],
	)


def _chunk(
	conn: sqlite3.Connection,
	*,
	pid: str,
	source_id: str,
	number: str,
	inserted_into: str | None = None,
	char_count: int = 100,
	partial: bool = False,
) -> None:
	meta = {
		"source_id": source_id,
		"provision_id": pid,
		"unit_type": "article",
		"unit_number": number,
		"unit_label": f"Article {number}",
	}
	if inserted_into:
		meta["inserted_into"] = inserted_into
	if partial:
		meta["provision_partial"] = True
	conn.execute(
		"""INSERT INTO chunks(
			chunk_id, doc_id, version_id, chunk_index, text, char_count,
			token_estimate, qdrant_id, metadata_json, created_at
		) VALUES (?,?,?,?,?,?,?,?,?,?)""",
		[
			f"{source_id}-{number}-{inserted_into or 'base'}",
			source_id,
			f"{source_id}-v1",
			int(number),
			f"Article {number}. Text.",
			char_count,
			char_count // 4,
			f"{source_id}-{number}",
			json.dumps(meta),
			"now",
		],
	)


def _patch_sources(monkeypatch, sources: list[SourceConfig]) -> None:
	monkeypatch.setattr("app.indexing.consolidation.load_allowed_sources", lambda: sources)
	monkeypatch.setattr("app.indexing.amendment_timeline.load_allowed_sources", lambda: sources)


def test_bucket_classification_reports_exclusions(tmp_path, monkeypatch):
	conn = _conn()
	sources = [_source("base")]
	for source_id in (
		"amend_ok",
		"amend_partial",
		"amend_chain_a",
		"amend_outlier",
		"amend_nobase",
		"amend_override",
	):
		sources.append(_source(source_id, "2020-01-01", amends=["base"]))
		path = tmp_path / f"{source_id}.txt"
		path.write_text('"Article 1. Replacement text.')
		_version(conn, source_id, path)
	sources.append(_source("amend_chain_b", "2021-01-01", amends=["base"]))
	path = tmp_path / "amend_chain_b.txt"
	path.write_text('"Article 1. Replacement text.')
	_version(conn, "amend_chain_b", path)
	_version(conn, "base", tmp_path / "base.txt")
	_patch_sources(monkeypatch, sources)
	monkeypatch.setattr(
		"app.indexing.consolidation.load_provision_overrides",
		lambda: {"base:article:6": (object(),)},
	)

	for n in (1, 2, 3, 4, 6):
		_chunk(conn, pid=f"base:article:{n}", source_id="base", number=str(n), char_count=100)
	_chunk(conn, pid="base:article:1", source_id="amend_ok", number="1", inserted_into="base", char_count=100)
	_chunk(conn, pid="base:article:2", source_id="amend_partial", number="2", inserted_into="base", partial=True)
	_chunk(conn, pid="base:article:3", source_id="amend_chain_a", number="3", inserted_into="base")
	_chunk(conn, pid="base:article:3", source_id="amend_chain_b", number="3", inserted_into="base")
	_chunk(conn, pid="base:article:4", source_id="amend_outlier", number="4", inserted_into="base", char_count=50)
	_chunk(conn, pid="base:article:5", source_id="amend_nobase", number="5", inserted_into="base")
	_chunk(conn, pid="base:article:6", source_id="amend_override", number="6", inserted_into="base")

	plan = build_splice_plan(conn)

	assert [s.key for s in plan.splices_by_base_doc["base"]] == ["base:article:1"]
	reasons = {item["key"]: item["reason"] for item in plan.exclusions}
	assert reasons["base:article:2"] == "partial"
	assert reasons["base:article:3"] == "chain"
	assert reasons["base:article:4"] == "ratio_outlier"
	assert reasons["base:article:5"] == "no_base"
	assert reasons["base:article:6"] == "override_collision"


def test_preflight_partial_mismatch_excludes_candidate(tmp_path, monkeypatch):
	conn = _conn()
	sources = [_source("base"), _source("amend", "2020-01-01", amends=["base"])]
	_patch_sources(monkeypatch, sources)
	monkeypatch.setattr("app.indexing.consolidation.load_provision_overrides", lambda: {})
	amend_path = tmp_path / "amend.txt"
	amend_path.write_text('"Article 1. Replacement text.\nx x x.')
	_version(conn, "base", tmp_path / "base.txt")
	_version(conn, "amend", amend_path)
	_chunk(conn, pid="base:article:1", source_id="base", number="1", char_count=100)
	_chunk(conn, pid="base:article:1", source_id="amend", number="1", inserted_into="base", char_count=100)

	plan = build_splice_plan(conn)

	assert plan.splices_by_base_doc == {}
	assert plan.preflight_mismatches == ({
		"key": "base:article:1",
		"source_id": "amend",
		"stored_partial": False,
		"recomputed_partial": True,
	},)
	assert plan.exclusions[0]["reason"] == "preflight_mismatch"


def test_dequote_and_splice_preserves_structure_and_internal_quotes():
	base_text = "Article 1. Old penalty.\nArticle 2. Next rule.\n"
	base_meta = {"source_id": "base", "structure": "hierarchical"}
	amendment_text = (
		"Section 1. Intro.\n"
		'"Article 1. New penalty with "internal quote" preserved.\n'
		'"Second paragraph.\n'
		'Final sentence".\n'
		"Section 2. Effectivity.\n"
	)
	splice = Splice(
		key="base:article:1",
		base_source_id="base",
		amendment_source_id="amend",
		amendment_official_number="RA 1",
		amendment_approval_date="2020-01-01",
		amendment_provision_id="base:article:1",
		unit_type="article",
		unit_number="1",
	)

	out = consolidate(
		base_text,
		provision_spans(base_text, base_meta),
		(splice,),
		{"amend": amendment_text},
	)

	assert "Article 1. New penalty" in out
	assert '"internal quote"' in out
	assert "[as amended by RA 1, approved 2020-01-01]" in out
	assert "Article 1. Old penalty" not in out
	assert any(span.provision_id == "base:article:1" for span in provision_spans(out, base_meta))


def test_back_to_front_replacement_handles_multiple_splices():
	base_text = "Article 1. Old one.\nArticle 2. Old two.\nArticle 3. Keep.\n"
	base_meta = {"source_id": "base", "structure": "hierarchical"}
	amendment_text = (
		'"Article 1. New one.\n'
		'"Article 2. New two.\n'
		"Section 1. Effectivity.\n"
	)
	splices = (
		Splice("base:article:1", "base", "amend", "RA", "2020-01-01", unit_type="article", unit_number="1"),
		Splice("base:article:2", "base", "amend", "RA", "2020-01-01", unit_type="article", unit_number="2"),
	)

	out = consolidate(
		base_text,
		provision_spans(base_text, base_meta),
		splices,
		{"amend": amendment_text},
	)

	assert "Article 1. New one." in out
	assert "Article 2. New two." in out
	assert "Article 3. Keep." in out
	assert "Old one" not in out
	assert "Old two" not in out


def test_index_document_stamps_base_parent_and_amendment_chunks(tmp_path, monkeypatch):
	conn = _conn()
	monkeypatch.setattr(settings, "chunk_size", 12)
	monkeypatch.setattr(settings, "chunk_overlap", 2)
	monkeypatch.setattr("app.indexing.index_service.get_qdrant_client", lambda: object())
	monkeypatch.setattr("app.indexing.index_service.ensure_collection", lambda client: None)
	monkeypatch.setattr("app.indexing.index_service.embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
	monkeypatch.setattr("app.indexing.index_service.delete_by_doc_id", lambda *args: None)
	monkeypatch.setattr("app.indexing.index_service.upsert_nodes", lambda *args: None)
	monkeypatch.setattr("app.indexing.index_service.build_and_save", lambda nodes: None)
	monkeypatch.setattr("app.indexing.index_service.load_provision_overrides", lambda: {})

	base_text = (
		"Article 1. Old chapeau long enough for enumeration splitting.\n"
		"(a) Old item one.\n"
		"(b) Old item two.\n"
	)
	amend_text = (
		'"Article 1. New chapeau long enough for enumeration splitting.\n'
		"(a) New item one.\n"
		"(b) New item two.\n"
	)
	amend_path = tmp_path / "amend.txt"
	amend_path.write_text(amend_text)
	_version(conn, "amend", amend_path)
	splice = Splice(
		key="base:article:1",
		base_source_id="base",
		amendment_source_id="amend",
		amendment_official_number="RA 1",
		amendment_approval_date="2020-01-01",
		amendment_provision_id="base:article:1",
		unit_type="article",
		unit_number="1",
	)
	plan = SplicePlan(
		splices_by_base_doc={"base": (splice,)},
		hidden_keys_by_amendment={"amend": ("base:article:1",)},
		exclusions=(),
		preflight_mismatches=(),
	)

	index_document(
		conn,
		doc_id="base",
		text=base_text,
		source_metadata={"doc_id": "base", "source_id": "base", "title": "Base", "structure": "hierarchical"},
		version_id="base-v1",
		splice_plan=plan,
	)
	base_meta = [json.loads(row["metadata_json"]) for row in conn.execute("SELECT metadata_json FROM chunks")]
	parent_text = conn.execute("SELECT text FROM chunk_parents WHERE doc_id = 'base'").fetchone()["text"]
	assert base_meta
	assert all(meta.get("consolidated") == 1 for meta in base_meta if meta.get("provision_id") == "base:article:1")
	assert all(meta.get("amended_by") == ["amend"] for meta in base_meta if meta.get("provision_id") == "base:article:1")
	assert "New chapeau" in parent_text
	assert "Old chapeau" not in parent_text

	index_document(
		conn,
		doc_id="amend",
		text=amend_text,
		source_metadata={
			"doc_id": "amend",
			"source_id": "amend",
			"title": "Amendment",
			"structure": "hierarchical",
			"amends": ["base"],
		},
		version_id="amend-v1",
		splice_plan=plan,
	)
	amend_meta = [
		json.loads(row["metadata_json"])
		for row in conn.execute("SELECT metadata_json FROM chunks WHERE doc_id = 'amend'")
	]
	assert amend_meta
	assert all(meta["operability_action"] == "hide" for meta in amend_meta if meta.get("inserted_into"))
	assert all(meta["operability_basis"] == "consolidated" for meta in amend_meta if meta.get("inserted_into"))


def test_coherence_check_raises_on_half_consolidated_state():
	conn = _conn()
	splice = Splice("base:article:1", "base", "amend", "RA", "2020-01-01")
	plan = SplicePlan({"base": (splice,)}, {"amend": ("base:article:1",)}, (), ())
	_chunk(conn, pid="base:article:1", source_id="base", number="1")
	_chunk(conn, pid="base:article:1", source_id="amend", number="1", inserted_into="base")

	with pytest.raises(RuntimeError, match="coherence failed"):
		check_consolidation_coherence(conn, plan)


def test_doc_scoped_reindex_auto_expands_to_partner_docs(tmp_path, monkeypatch, capsys):
	from app.indexing import reindex as reindex_module

	db_path = tmp_path / "raglab.db"
	conn = sqlite3.connect(db_path)
	conn.executescript(
		"""
		CREATE TABLE documents(source_id TEXT, doc_id TEXT);
		CREATE TABLE document_versions(
			version_id TEXT,
			doc_id TEXT,
			fetched_at TEXT,
			normalized_path TEXT
		);
		CREATE TABLE chunk_parents(doc_id TEXT);
		"""
	)
	for source_id in ("base", "amend"):
		path = tmp_path / f"{source_id}.txt"
		path.write_text("Article 1. Text.")
		conn.execute("INSERT INTO documents(source_id, doc_id) VALUES (?,?)", [source_id, source_id])
		conn.execute(
			"INSERT INTO document_versions VALUES (?,?,?,?)",
			[f"{source_id}-v1", source_id, "2026-01-01", str(path)],
		)
	conn.commit()
	conn.close()
	monkeypatch.setattr(settings, "db_path", str(db_path))
	monkeypatch.setattr(reindex_module, "_require_services", lambda: None)
	monkeypatch.setattr(
		reindex_module,
		"load_allowed_sources",
		lambda: [_source("base"), _source("amend", "2020-01-01", amends=["base"])],
	)
	splice = Splice("base:article:1", "base", "amend", "RA", "2020-01-01")
	plan = SplicePlan({"base": (splice,)}, {"amend": ("base:article:1",)}, (), ())
	monkeypatch.setattr(reindex_module, "build_splice_plan", lambda conn: plan)
	indexed: list[str] = []

	def fake_index_document(conn, doc_id, text, source_metadata, version_id, splice_plan=None):
		indexed.append(source_metadata["source_id"])
		return 1

	monkeypatch.setattr(reindex_module, "index_document", fake_index_document)

	reindex_module.reindex("base")

	assert indexed == ["base", "amend"]
	assert "base <-> amend reindexed together" in capsys.readouterr().out


def test_consolidate_same_inputs_is_byte_identical():
	base_text = "Article 1. Old.\nArticle 2. Next.\n"
	base_meta = {"source_id": "base", "structure": "hierarchical"}
	amend_text = '"Article 1. New.\nSection 1. Effectivity.\n'
	splice = Splice("base:article:1", "base", "amend", "RA", "2020-01-01", unit_type="article", unit_number="1")
	args = (base_text, provision_spans(base_text, base_meta), (splice,), {"amend": amend_text})

	assert consolidate(*args) == consolidate(*args)
