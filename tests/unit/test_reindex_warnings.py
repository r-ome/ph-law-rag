import json
import sqlite3

import pytest

from app.indexing.provision_status import ProvisionOverride
from app.indexing.reindex import _warn_unmatched_overrides

pytestmark = pytest.mark.unit


def test_override_guard_warns_on_multi_source_provision_id(monkeypatch, capsys):
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute("CREATE TABLE chunks(metadata_json TEXT)")
	pid = "revised_penal_code:article:309"
	for source_id in ("revised_penal_code", "criminal_penalties_amendment_2017"):
		conn.execute(
			"INSERT INTO chunks(metadata_json) VALUES (?)",
			[json.dumps({"source_id": source_id, "provision_id": pid})],
		)
	override = ProvisionOverride(
		provision_id=pid,
		source_id=None,
		unit_labels=None,
		provision_status="superseded",
		operability_action="hide",
		basis_source_id=None,
		effective_date=None,
		note=None,
	)
	monkeypatch.setattr("app.indexing.reindex.load_provision_overrides", lambda: {pid: (override,)})

	_warn_unmatched_overrides(conn)

	out = capsys.readouterr().out
	assert "matches chunks from multiple source_id values" in out
	assert "provision_supersession.yaml" in out


def test_override_guard_allows_scoped_multi_source_provision_id(monkeypatch, capsys):
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute("CREATE TABLE chunks(metadata_json TEXT)")
	pid = "dangerous_drugs_act:article-ii:section:21"
	for source_id in ("dangerous_drugs_act", "dangerous_drugs_amendments_2014"):
		conn.execute(
			"INSERT INTO chunks(metadata_json) VALUES (?)",
			[json.dumps({"source_id": source_id, "provision_id": pid})],
		)
	override = ProvisionOverride(
		provision_id=pid,
		source_id="dangerous_drugs_act",
		unit_labels=("Section 21",),
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="dangerous_drugs_amendments_2014",
		effective_date=None,
		note=None,
	)
	monkeypatch.setattr("app.indexing.reindex.load_provision_overrides", lambda: {pid: (override,)})

	_warn_unmatched_overrides(conn)

	out = capsys.readouterr().out
	assert "matches chunks from multiple source_id values" not in out


def test_override_guard_warns_on_unmatched_unit_labels(monkeypatch, capsys):
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute("CREATE TABLE chunks(metadata_json TEXT)")
	pid = "dangerous_drugs_act:article-ii:section:21"
	conn.execute(
		"INSERT INTO chunks(metadata_json) VALUES (?)",
		[json.dumps({
			"source_id": "dangerous_drugs_act",
			"provision_id": pid,
			"unit_label": "Section 21(1)",
		})],
	)
	override = ProvisionOverride(
		provision_id=pid,
		source_id="dangerous_drugs_act",
		unit_labels=("Section 21(1)", "Section 21(99)"),
		provision_status="superseded",
		operability_action="hide",
		basis_source_id="dangerous_drugs_amendments_2014",
		effective_date=None,
		note=None,
	)
	monkeypatch.setattr("app.indexing.reindex.load_provision_overrides", lambda: {pid: (override,)})

	_warn_unmatched_overrides(conn)

	out = capsys.readouterr().out
	assert "unit_labels matched ZERO chunks" in out
	assert "Section 21(99)" in out
	assert "Section 21(1)" not in out
