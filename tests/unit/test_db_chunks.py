from datetime import datetime, timezone

from app.config import settings
from app.db import get_chunks_by_ids, get_connection, init_db


def _seed(conn, doc_id, chunk_id, chunk_index, text):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO documents(doc_id, source_id, title, url, doc_type, file_format, category, "
        "tags_json, enabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, doc_id, f"Title {doc_id}", "http://x", "statute", "html", "civil", "[]", 1, now, now),
    )
    conn.execute(
        "INSERT INTO document_versions(version_id, doc_id, fetched_at, http_status, content_hash, "
        "content_length, raw_path, normalized_path, extraction_method, changed_from_previous) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"v-{doc_id}", doc_id, now, 200, "hash", 10, "raw", "norm", "trafilatura", 1),
    )
    conn.execute(
        "INSERT INTO chunks(chunk_id, doc_id, version_id, chunk_index, text, char_count, "
        "token_estimate, qdrant_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (chunk_id, doc_id, f"v-{doc_id}", chunk_index, text, len(text), len(text) // 4, chunk_id, "{}", now),
    )


def test_get_chunks_by_ids_dedupes_preserves_order_reports_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "raglab.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    init_db()
    conn = get_connection()
    try:
        _seed(conn, "doc-1", "chunk-1", 0, "text one")
        _seed(conn, "doc-2", "chunk-2", 0, "text two")
        conn.commit()
    finally:
        conn.close()

    result = get_chunks_by_ids(["chunk-1", "ghost", "chunk-1"])

    assert [c["chunk_id"] for c in result] == ["chunk-1"]

    hits = {c["chunk_id"]: c for c in get_chunks_by_ids(["chunk-1", "chunk-2"])}
    assert set(hits) == {"chunk-1", "chunk-2"}


def test_get_chunks_by_ids_empty_input():
    assert get_chunks_by_ids([]) == []
