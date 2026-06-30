"""Force a rebuild of the index from already-fetched normalized text.

Unlike `sync`, this does NO HTTP fetch, NO hash comparison, and writes NO new
document_versions — so a chunker/embedding change can be applied to the existing
corpus without re-downloading (and without losing sources that now 403). It reads
each document's latest normalized text from disk and re-runs `index_document`,
which rebuilds chunks, chunk_parents, Qdrant vectors, and BM25. Safe to rerun.
"""

from pathlib import Path

from app.config import settings, load_allowed_sources
from app.db import get_connection
from app.ingestion.sync import build_source_metadata
from app.indexing.index_service import index_document
from app.indexing.provision_status import load_provision_overrides


def _warn_unmatched_overrides(conn) -> None:
    """Zero-match guard: a provision_status override keyed on a provision_id that no chunk
    carries is a silent dead rule (typo / renumbered / unsynced source). Warn after a full
    reindex so a bad override is visible rather than quietly inert."""
    overrides = load_provision_overrides()
    if not overrides:
        return
    rows = conn.execute(
        "SELECT DISTINCT json_extract(metadata_json, '$.provision_id') AS pid FROM chunks"
    ).fetchall()
    present = {r["pid"] for r in rows if r["pid"]}
    missing = [pid for pid in overrides if pid not in present]
    if missing:
        print(f"[WARN] {len(missing)} provision override(s) matched ZERO chunks "
              f"(typo/renumber/unsynced): {', '.join(missing)}")
    print(f"[overrides] {len(overrides) - len(missing)}/{len(overrides)} provision overrides matched ≥1 chunk")


def _require_services() -> None:
    """Reindex still embeds + upserts, so fail loudly if the backends are down."""
    from app.api.health_query import _qdrant_ok, ping_url

    if not _qdrant_ok():
        raise RuntimeError(f"Qdrant not reachable at {settings.qdrant_url} - start it before reindexing")
    if settings.embedding_backend == "ollama":
        if not ping_url(f"{settings.ollama_base_url}/api/version"):
            raise RuntimeError(f"Ollama not reachable at {settings.ollama_base_url} — start it before reindexing.")


def _latest_version(conn, source_id: str):
    return conn.execute(
        """
        SELECT d.doc_id, v.version_id, v.normalized_path
        FROM documents d
        JOIN document_versions v ON v.doc_id = d.doc_id
        WHERE d.source_id = ?
        ORDER BY v.fetched_at DESC
        LIMIT 1
        """,
        [source_id],
    ).fetchone()


def reindex(doc_id: str | None = None) -> list[dict]:
    """Reindex every enabled source (or just one matching `doc_id`/source_id)
    from disk. Prints per-source status; returns counts."""
    _require_services()

    results: list[dict] = []
    conn = get_connection()
    try:
        for source in load_allowed_sources():
            row = _latest_version(conn, source.source_id)
            if row is None:
                print(f"[SKIP] {source.source_id} — no indexed version (run sync first)")
                continue
            if doc_id and doc_id not in (row["doc_id"], source.source_id):
                continue

            path = Path(row["normalized_path"])
            if not path.exists():
                print(f"[FAIL] {source.source_id} — normalized file missing: {path}")
                continue

            text = path.read_text()
            chunks = index_document(
                conn=conn,
                doc_id=row["doc_id"],
                text=text,
                source_metadata=build_source_metadata(source, row["doc_id"]),
                version_id=row["version_id"],
            )
            parents = conn.execute(
                "SELECT COUNT(*) AS c FROM chunk_parents WHERE doc_id = ?", [row["doc_id"]]
            ).fetchone()["c"]
            conn.commit()
            print(f"[OK] {source.source_id} indexed {chunks} chunks, {parents} parents")
            results.append({"source_id": source.source_id, "chunks": chunks, "parents": parents})

        if doc_id is None:  # full reindex → check every override resolved to real chunks
            _warn_unmatched_overrides(conn)
    finally:
        conn.close()

    if doc_id and not results:
        print(f"[WARN] no source matched '{doc_id}'")
    return results
