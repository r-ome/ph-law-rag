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
        
    # index_document is faked to write one realistic chunk row (with per-chunk metadata
    # keys) so the metadata-refresh path has chunks to reconcile, without touching Qdrant.
    def fake_index(conn, doc_id, text, source_metadata, version_id):
        import json as _json
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM chunk_parents WHERE doc_id = ?", [doc_id])
        conn.execute(
            """INSERT INTO chunks(chunk_id, doc_id, version_id, chunk_index, text,
                char_count, token_estimate, qdrant_id, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [f"{doc_id}-0", doc_id, version_id, 0, text, len(text), len(text) // 4,
             f"{doc_id}-0",
             _json.dumps({**source_metadata, "is_structural": False, "part_index": 0}),
             "now"],
        )
        conn.execute(
            """INSERT INTO chunk_parents(parent_key, doc_id, source_id, title, url,
                unit_type, unit_label, structure_path, text, char_count, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [f"{doc_id}-p", doc_id, source_metadata.get("source_id"),
             source_metadata.get("title"), source_metadata.get("url"),
             None, None, None, text, len(text), "now"],
        )
        return 1

    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [_source()])
    monkeypatch.setattr(sync_module, "fetch_source", fake_fetch)
    monkeypatch.setattr(sync_module, "parse_html", lambda content, url: content.decode("utf-8"))
    monkeypatch.setattr(sync_module, "save_raw_fetch", lambda *a, **k: "data/raw/civil_code.html")
    monkeypatch.setattr(sync_module, "save_normalized_document", lambda *a, **k: "data/normalized/civil_code.html")
    monkeypatch.setattr("app.indexing.index_service.index_document", fake_index)
    # Insulate the Tier A in-place refresh from real Qdrant / BM25.
    monkeypatch.setattr("app.indexing.index_service.get_qdrant_client", lambda: object())
    monkeypatch.setattr("app.indexing.index_service.refresh_doc_payload", lambda *a, **k: None)
    monkeypatch.setattr("app.indexing.index_service.build_and_save", lambda *a, **k: None)

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


def test_indexing_exception_is_isolated_and_run_recorded(sync_env, monkeypatch):
    # An exception while indexing one source must not abort run_sync, and the
    # sync_runs row must still be written (constraint: never raise inside run_sync).
    _, db_path = sync_env

    def boom(**kwargs):
        raise RuntimeError("qdrant exploded")

    monkeypatch.setattr("app.indexing.index_service.index_document", boom)

    counts = run_sync()  # must not raise

    assert counts["scanned"] == 1
    assert counts["failed"] == 1
    assert counts["changed"] == 0
    rows = _query(db_path, "SELECT scanned_count, failed_count, status FROM sync_runs")
    assert len(rows) == 1
    assert rows[0] == (1, 1, "completed")
    # the failed source left no partial document behind (rolled back on close)
    assert _query(db_path, "SELECT * FROM documents") == []


def test_url_change_keeps_same_document(sync_env, monkeypatch):
    # Same source_id with a changed url is the SAME document (a change), not a new
    # one — identity is keyed on source_id, not the mutable url.
    state, db_path = sync_env
    run_sync()

    changed = _source()
    changed.url = "https://www.example.test/civil-code-v2"
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [changed])
    state["text"] = "Article 1318 (relocated). Updated requisites."

    counts = run_sync()

    assert counts["changed"] == 1
    assert len(_query(db_path, "SELECT doc_id FROM documents")) == 1


def test_metadata_refresh_persists_on_unchanged_content(sync_env, monkeypatch):
    # A url/title change with identical content is "unchanged" for versioning, but
    # the mutable manifest metadata must still be persisted to the documents row.
    _, db_path = sync_env
    run_sync()

    changed = _source()
    changed.url = "https://www.example.test/civil-code-RELOCATED"
    changed.title = "Civil Code (relocated)"
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [changed])

    counts = run_sync()  # content identical; title baked into text -> Tier B re-index

    assert counts["reindexed_meta"] == 1
    assert counts["changed"] == 0
    # no spurious version row from a metadata-only reconcile
    assert len(_query(db_path, "SELECT version_id FROM document_versions")) == 1
    rows = _query(db_path, "SELECT url, title FROM documents")
    assert rows[0][0] == "https://www.example.test/civil-code-RELOCATED"
    assert rows[0][1] == "Civil Code (relocated)"


def test_tier_a_url_change_refreshes_in_place(sync_env, monkeypatch):
    # url is not baked into chunk text -> in-place payload refresh, NO re-embed and NO
    # new version. Chunk metadata + chunk_parents.url update; per-chunk keys preserved.
    import json
    _, db_path = sync_env
    run_sync()

    captured = {}
    monkeypatch.setattr(
        "app.indexing.index_service.refresh_doc_payload",
        lambda client, doc_id, fields: captured.update({"doc_id": doc_id, "fields": fields}),
    )
    # index_document must NOT be called on the Tier A path
    monkeypatch.setattr(
        "app.indexing.index_service.index_document",
        lambda **k: (_ for _ in ()).throw(AssertionError("re-embed on Tier A")),
    )

    changed = _source()
    changed.url = "https://www.example.test/civil-code-NEW"
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [changed])

    counts = run_sync()

    assert counts["refreshed"] == 1
    assert captured["fields"] == {"url": "https://www.example.test/civil-code-NEW"}
    assert len(_query(db_path, "SELECT version_id FROM document_versions")) == 1
    meta = json.loads(_query(db_path, "SELECT metadata_json FROM chunks")[0][0])
    assert meta["url"] == "https://www.example.test/civil-code-NEW"
    assert meta["part_index"] == 0  # per-chunk key preserved
    assert "is_structural" in meta
    assert _query(db_path, "SELECT url FROM chunk_parents")[0][0] == "https://www.example.test/civil-code-NEW"


def test_metadata_reconcile_folded_into_sync_runs_unchanged(sync_env, monkeypatch):
    # sync_runs has no metadata-only columns; a Tier A refresh must be folded into
    # unchanged_count so scanned = changed + unchanged + failed still holds.
    _, db_path = sync_env
    run_sync()

    changed = _source()
    changed.url = "https://www.example.test/civil-code-FOLD"
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [changed])

    counts = run_sync()

    assert counts["refreshed"] == 1
    row = _query(
        db_path,
        "SELECT scanned_count, changed_count, unchanged_count, failed_count "
        "FROM sync_runs ORDER BY rowid DESC LIMIT 1",
    )[0]
    scanned, changed_c, unchanged_c, failed_c = row
    assert unchanged_c == 1
    assert scanned == changed_c + unchanged_c + failed_c


def test_no_metadata_change_skips(sync_env):
    _, db_path = sync_env
    run_sync()
    counts = run_sync()
    assert counts["unchanged"] == 1
    assert counts["refreshed"] == 0
    assert counts["reindexed_meta"] == 0


def test_tier_a_qdrant_failure_counts_failed_no_commit(sync_env, monkeypatch):
    # If the in-place Qdrant refresh fails, the source is counted failed and the chunk
    # metadata is NOT committed (no partial cross-store drift).
    import json
    _, db_path = sync_env
    run_sync()
    before = json.loads(_query(db_path, "SELECT metadata_json FROM chunks")[0][0])["url"]

    def boom(*a, **k):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr("app.indexing.index_service.refresh_doc_payload", boom)
    changed = _source()
    changed.url = "https://www.example.test/should-not-stick"
    monkeypatch.setattr(sync_module, "load_allowed_sources", lambda: [changed])

    counts = run_sync()  # must not raise

    assert counts["failed"] == 1
    assert counts["refreshed"] == 0
    after = json.loads(_query(db_path, "SELECT metadata_json FROM chunks")[0][0])["url"]
    assert after == before  # rolled back, no partial write persisted
