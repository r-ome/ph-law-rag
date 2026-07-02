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
		provision_status="superseded",
		operability_action="hide",
		basis_source_id=None,
		effective_date=None,
		note=None,
	)
	monkeypatch.setattr("app.indexing.reindex.load_provision_overrides", lambda: {pid: override})

	_warn_unmatched_overrides(conn)

	out = capsys.readouterr().out
	assert "matches chunks from multiple source_id values" in out
	assert "provision_supersession.yaml" in out
