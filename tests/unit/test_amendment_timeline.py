import json
import sqlite3

import pytest

from app.config import SourceConfig
from app.indexing import amendment_timeline

pytestmark = pytest.mark.unit


def _source(source_id: str, approval_date: str | None) -> SourceConfig:
	return SourceConfig(
		source_id=source_id,
		enabled=True,
		file_format="html",
		url=f"https://example.test/{source_id}",
		category="statute",
		doc_type="statute",
		tags=[],
		title=source_id,
		approval_date=approval_date,
		status="operative",
		source_index="lawphil",
	)


def _conn() -> sqlite3.Connection:
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute(
		"""
			CREATE TABLE chunks(
				chunk_index INTEGER,
				char_count INTEGER,
				metadata_json TEXT
			)
		"""
	)
	return conn


def _insert(
	conn: sqlite3.Connection,
	*,
	pid: str,
	source_id: str,
	chunk_index: int = 0,
	char_count: int = 100,
	unit_type: str = "section",
	unit_number: str = "1",
	unit_label: str = "Section 1",
	inserted_into: str | None = None,
	provision_partial: bool = False,
) -> None:
	meta = {
		"provision_id": pid,
		"source_id": source_id,
		"unit_type": unit_type,
		"unit_number": unit_number,
		"unit_label": unit_label,
	}
	if inserted_into:
		meta["inserted_into"] = inserted_into
	if provision_partial:
		meta["provision_partial"] = True
	conn.execute(
		"INSERT INTO chunks(chunk_index, char_count, metadata_json) VALUES (?,?,?)",
		[chunk_index, char_count, json.dumps(meta)],
	)


def _patch_sources(monkeypatch, *sources: SourceConfig) -> None:
	monkeypatch.setattr(amendment_timeline, "load_allowed_sources", lambda: list(sources))


def test_pathless_insertion_with_unique_path_scoped_base_merges(monkeypatch):
	conn = _conn()
	base_pid = "dangerous_drugs_act:article-ii:section:21"
	insert_pid = "dangerous_drugs_act:section:21"
	_insert(
		conn,
		pid=base_pid,
		source_id="dangerous_drugs_act",
		unit_number="21",
		unit_label="Section 21",
		char_count=200,
	)
	_insert(
		conn,
		pid=insert_pid,
		source_id="dangerous_drugs_amendments_2014",
		unit_number="21",
		unit_label="Section 21",
		inserted_into="dangerous_drugs_act",
		char_count=50,
		provision_partial=True,
	)
	_patch_sources(
		monkeypatch,
		_source("dangerous_drugs_act", "2002-06-07"),
		_source("dangerous_drugs_amendments_2014", "2014-07-15"),
	)

	result = amendment_timeline.build_timelines(conn)

	timeline = result.timelines[base_pid]
	assert [entry.source_id for entry in timeline.entries] == [
		"dangerous_drugs_act",
		"dangerous_drugs_amendments_2014",
	]
	assert timeline.entries[0].is_insertion is False
	assert timeline.entries[1].is_insertion is True
	assert timeline.entries[1].provision_id == insert_pid
	assert timeline.entries[1].approval_date == "2014-07-15"
	assert timeline.entries[1].length_ratio == 0.25
	assert timeline.entries[1].provision_partial is True
	assert result.ambiguous_insertions == ()


def test_pathless_insertion_with_multiple_path_scoped_candidates_is_ambiguous(monkeypatch):
	conn = _conn()
	for pid in (
		"constitution_1987:article-iii:section:1",
		"constitution_1987:article-vi:section:1",
	):
		_insert(
			conn,
			pid=pid,
			source_id="constitution_1987",
			unit_number="1",
			unit_label="Section 1",
		)
	_insert(
		conn,
		pid="constitution_1987:section:1",
		source_id="constitution_amendment",
		unit_number="1",
		unit_label="Section 1",
		inserted_into="constitution_1987",
	)
	_patch_sources(
		monkeypatch,
		_source("constitution_1987", "1987-02-02"),
		_source("constitution_amendment", "2020-01-01"),
	)

	result = amendment_timeline.build_timelines(conn)

	assert result.ambiguous_insertions == ({
		"pid": "constitution_1987:section:1",
		"source_id": "constitution_amendment",
		"candidates": [
			"constitution_1987:article-iii:section:1",
			"constitution_1987:article-vi:section:1",
		],
	},)
	assert "constitution_1987:section:1" not in result.timelines
	assert all(len(timeline.entries) == 1 for timeline in result.timelines.values())


def test_insertion_with_zero_base_candidates_gets_single_entry_timeline(monkeypatch):
	conn = _conn()
	pid = "revised_penal_code:article:266-a"
	_insert(
		conn,
		pid=pid,
		source_id="anti_rape_law_1997",
		unit_type="article",
		unit_number="266-A",
		unit_label="Article 266-A",
		inserted_into="revised_penal_code",
	)
	_patch_sources(monkeypatch, _source("anti_rape_law_1997", "1997-09-30"))

	result = amendment_timeline.build_timelines(conn)

	assert list(result.timelines) == [pid]
	assert result.timelines[pid].entries[0].source_id == "anti_rape_law_1997"
	assert result.timelines[pid].entries[0].is_insertion is True


def test_chain_of_two_insertions_orders_by_date_and_computes_ratios(monkeypatch):
	conn = _conn()
	pid = "anti_trafficking:section:6"
	_insert(conn, pid=pid, source_id="anti_trafficking", char_count=100, unit_number="6")
	_insert(
		conn,
		pid=pid,
		source_id="expanded_anti_trafficking_2013",
		char_count=50,
		unit_number="6",
		inserted_into="anti_trafficking",
	)
	_insert(
		conn,
		pid=pid,
		source_id="expanded_anti_trafficking_2022",
		char_count=100,
		unit_number="6",
		inserted_into="anti_trafficking",
	)
	_patch_sources(
		monkeypatch,
		_source("anti_trafficking", "2003-05-26"),
		_source("expanded_anti_trafficking_2013", "2013-02-06"),
		_source("expanded_anti_trafficking_2022", "2022-06-23"),
	)

	result = amendment_timeline.build_timelines(conn)

	entries = result.timelines[pid].entries
	assert [entry.approval_date for entry in entries] == [
		"2003-05-26",
		"2013-02-06",
		"2022-06-23",
	]
	assert [entry.length_ratio for entry in entries] == [None, 0.5, 2.0]


def test_same_date_insertions_are_conflicts_and_excluded(monkeypatch):
	conn = _conn()
	pid = "target_law:section:9"
	_insert(conn, pid=pid, source_id="target_law", unit_number="9")
	for source_id in ("amendment_a", "amendment_b"):
		_insert(
			conn,
			pid=pid,
			source_id=source_id,
			unit_number="9",
			inserted_into="target_law",
		)
	_patch_sources(
		monkeypatch,
		_source("target_law", "2000-01-01"),
		_source("amendment_a", "2020-01-01"),
		_source("amendment_b", "2020-01-01"),
	)

	result = amendment_timeline.build_timelines(conn)

	assert pid not in result.timelines
	assert result.same_date_conflicts == ({
		"key": pid,
		"source_ids": ("amendment_a", "amendment_b"),
		"date": "2020-01-01",
	},)


def test_missing_approval_date_on_multi_entry_key_is_excluded(monkeypatch):
	conn = _conn()
	pid = "target_law:section:2"
	_insert(conn, pid=pid, source_id="target_law", unit_number="2")
	_insert(
		conn,
		pid=pid,
		source_id="undated_amendment",
		unit_number="2",
		inserted_into="target_law",
	)
	_patch_sources(
		monkeypatch,
		_source("target_law", "2000-01-01"),
		_source("undated_amendment", None),
	)

	result = amendment_timeline.build_timelines(conn)

	assert pid not in result.timelines
	assert result.missing_dates == ({"key": pid, "source_id": "undated_amendment"},)


def test_aggregation_is_deterministic_by_chunk_index(monkeypatch):
	conn = _conn()
	pid = "civil_code:article:10"
	_insert(
		conn,
		pid=pid,
		source_id="civil_code",
		unit_type="article",
		unit_number="10",
		unit_label="Article 10-B",
		chunk_index=2,
		char_count=10,
	)
	_insert(
		conn,
		pid=pid,
		source_id="civil_code",
		unit_type="article",
		unit_number="10",
		unit_label="Article 10-A",
		chunk_index=1,
		char_count=20,
		provision_partial=True,
	)
	_insert(
		conn,
		pid=pid,
		source_id="civil_code",
		unit_type="article",
		unit_number="10",
		unit_label="Article 10-A",
		chunk_index=3,
		char_count=30,
	)
	_patch_sources(monkeypatch, _source("civil_code", "1949-06-18"))

	result = amendment_timeline.build_timelines(conn)

	entry = result.timelines[pid].entries[0]
	assert entry.unit_labels == ("Article 10-A", "Article 10-B")
	assert entry.provision_partial is True
	assert entry.char_count == 60


def test_exact_pid_collision_groups_without_pathless_resolution(monkeypatch):
	conn = _conn()
	pid = "anti_trafficking:section:6"
	_insert(conn, pid=pid, source_id="anti_trafficking", unit_number="6")
	_insert(
		conn,
		pid=pid,
		source_id="expanded_anti_trafficking_2013",
		unit_number="6",
		inserted_into="anti_trafficking",
	)
	_patch_sources(
		monkeypatch,
		_source("anti_trafficking", "2003-05-26"),
		_source("expanded_anti_trafficking_2013", "2013-02-06"),
	)

	result = amendment_timeline.build_timelines(conn)

	assert result.ambiguous_insertions == ()
	assert [entry.source_id for entry in result.timelines[pid].entries] == [
		"anti_trafficking",
		"expanded_anti_trafficking_2013",
	]
