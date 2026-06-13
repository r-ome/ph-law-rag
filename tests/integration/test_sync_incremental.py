import sqlite3
import pytest

import app.ingestion.sync as sync_module
from app.config import SourceConfig, settings
from app.db import init_db
from app.ingestion.fetcher import FetchResult
from app.ingestion.sync import run_sync

pytestmark = pytest.mark.integration

def _source() -> SourceConfig:
    return SourceConfig(
        source_id="civil_code",
        title="civil_code",
        url="https://www.example.test/civil-code",
        doc_type="republic_act",
        file_format="html",
        category="statute",
        tags=["civil"],
        enabled=True,
        status="operative",
        source_index="lawphil",
    )

@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    db_path = tmp_path/"raglab.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    init_db()
    
    state = {
        "text": "There is no contract unless requisites occur.",
    }
    
    def fake_fetch(source):
        return FetchResult(
            source_id=source.source_id,
            url=source.url,
            file_format=source.file_format,
            status="ok",
            http_status=200,
            content=state["text"].encode("utf-8"),
            error=None
        )
        
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [_source()])
    monkeypatch.setattr(sync_module, "fetch_source", fake_fetch)
    monkeypatch.setattr(sync_module, "parse_html", lambda content, url: content.decode("utf-8"))
    monkeypatch.setattr(sync_module, "save_raw_fetch", lambda *a, **k: "data/raw/civil_code.html")
    monkeypatch.setattr(sync_module, "save_normalized_document", lambda *a, **k: "data/normalized/civil_code.html")
    monkeypatch.setattr("app.indexing.index_service.index_document", lambda **k: 0)
    
    return state, db_path

def _query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
        
def test_first_sync_creates_one_version(sync_env):
    _, db_path = sync_env
    
    counts = run_sync()
    
    assert counts["scanned"] == 1
    assert counts["changed"] == 1
    assert counts["unchanged"] == 0
    assert counts["failed"] == 0
    rows = _query(db_path, "SELECT changed_from_previous FROM document_versions")
    assert len(rows) == 1
    assert rows[0][0] == 0
    
def test_unchanged_corpus_skips_second_run(sync_env):
    _, db_path = sync_env
    
    run_sync()
    counts = run_sync()
    
    assert counts["unchanged"] == 1
    assert counts["changed"] == 0
    rows = _query(db_path, "SELECT version_id FROM document_versions")
    assert len(rows) == 1
    
def test_changed_content_creates_new_version_flagged_changed(sync_env):
    state, db_path = sync_env
    
    run_sync()
    state["text"] = "Article 1318 (amended). New requisites now apply."
    counts = run_sync()
    
    assert counts["changed"] == 1
    rows = _query(
        db_path,
        "SELECT changed_from_previous FROM document_versions ORDER BY rowid"
    )
    assert len(rows) == 2
    assert rows[-1][0] == 1
    
def test_sync_run_row_recorded(sync_env):
    _, db_path = sync_env
    
    run_sync()
    rows = _query(db_path, "SELECT scanned_count, status from sync_runs")
    assert len(rows) == 1
    assert rows[-1][0] == 1
    assert rows[0][1] == "completed"
    assert rows[0][1] == "completed"
    
def test_failed_fetch_writes_no_version(sync_env, monkeypatch):
    _, db_path = sync_env
    monkeypatch.setattr(
        sync_module,
        "fetch_source",
        lambda source: FetchResult(
            source_id=source.source_id,
            url=source.url,
            file_format=source.file_format,
            status="failed",
            http_status=500,
            content=None,
            error="boom",
        ),
    )

    counts = run_sync()

    assert counts["failed"] == 1
    assert _query(db_path, "SELECT * FROM document_versions") == []
