import json
import sqlite3
from types import SimpleNamespace

import pytest
from llama_index.core.schema import TextNode

from app.indexing.index_service import report_amendment_indexing

pytestmark = pytest.mark.unit


def test_amendment_report_warns_on_inserted_base_collision(monkeypatch, capsys):
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute("CREATE TABLE chunks(metadata_json TEXT)")
	pid = "revised_penal_code:article:266-a"
	conn.execute(
		"INSERT INTO chunks(metadata_json) VALUES (?)",
		[json.dumps({"source_id": "revised_penal_code", "provision_id": pid})],
	)
	monkeypatch.setattr(
		"app.config.load_allowed_sources",
		lambda: [SimpleNamespace(source_id="revised_penal_code")],
	)
	node = TextNode(
		text="Article 266-A",
		metadata={
			"source_id": "anti_rape_law_1997",
			"provision_id": pid,
			"inserted_into": "revised_penal_code",
			"unit_type": "article",
		},
	)

	report_amendment_indexing(
		conn,
		{"source_id": "anti_rape_law_1997", "amends": ["revised_penal_code"]},
		[node],
	)

	out = capsys.readouterr().out
	assert "[SUPERSESSION-CANDIDATE] revised_penal_code:article:266-a" in out
	assert "inserted by anti_rape_law_1997" in out
