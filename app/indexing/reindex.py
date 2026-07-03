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
from app.indexing.index_service import index_document
from app.indexing.provision_status import load_provision_overrides
from app.indexing.consolidation import (
    build_splice_plan,
    check_consolidation_coherence,
)
from app.source_metadata import build_source_metadata


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
    for pid, rules in sorted(overrides.items()):
        rows = conn.execute(
            """
            SELECT DISTINCT json_extract(metadata_json, '$.source_id') AS source_id
            FROM chunks
            WHERE json_extract(metadata_json, '$.provision_id') = ?
            """,
            [pid],
        ).fetchall()
        source_ids = sorted(r["source_id"] for r in rows if r["source_id"])
        if len(source_ids) > 1 and any(rule.source_id is None for rule in rules):
            print(
                f"[WARN] provision_status override {pid} matches chunks from multiple "
                f"source_id values ({', '.join(source_ids)}); set source_id on the rule or use "
                "provision_supersession.yaml for same-id collisions"
            )
        for rule in rules:
            if not rule.unit_labels:
                continue
            if rule.source_id:
                label_rows = conn.execute(
                    """
                    SELECT DISTINCT json_extract(metadata_json, '$.unit_label') AS unit_label
                    FROM chunks
                    WHERE json_extract(metadata_json, '$.provision_id') = ?
                      AND json_extract(metadata_json, '$.source_id') = ?
                    """,
                    [pid, rule.source_id],
                ).fetchall()
            else:
                label_rows = conn.execute(
                    """
                    SELECT DISTINCT json_extract(metadata_json, '$.unit_label') AS unit_label
                    FROM chunks
                    WHERE json_extract(metadata_json, '$.provision_id') = ?
                    """,
                    [pid],
                ).fetchall()
            present_labels = {r["unit_label"] for r in label_rows if r["unit_label"]}
            missing_labels = [label for label in rule.unit_labels if label not in present_labels]
            if missing_labels:
                source_note = f" source_id={rule.source_id}" if rule.source_id else ""
                print(
                    f"[WARN] provision_status override {pid}{source_note} unit_labels matched "
                    f"ZERO chunks: {', '.join(missing_labels)}"
                )
    print(f"[overrides] {len(overrides) - len(missing)}/{len(overrides)} provision overrides matched ≥1 chunk")


def _warn_amendment_aggregate(conn) -> None:
    rows = conn.execute(
        """
        SELECT
            json_extract(metadata_json, '$.provision_id') AS provision_id,
            json_extract(metadata_json, '$.source_id') AS source_id,
            json_extract(metadata_json, '$.inserted_into') AS inserted_into,
            json_extract(metadata_json, '$.unit_type') AS unit_type,
            json_extract(metadata_json, '$.unit_number') AS unit_number
        FROM chunks
        WHERE json_extract(metadata_json, '$.provision_id') IS NOT NULL
        """
    ).fetchall()
    by_pid: dict[str, dict[str, set[str]]] = {}
    inserted_sections: set[tuple[str, str, str, str]] = set()
    target_pids: dict[str, set[str]] = {}
    for row in rows:
        pid = row["provision_id"]
        source_id = row["source_id"]
        inserted_into = row["inserted_into"]
        if not pid or not source_id:
            continue
        target_pids.setdefault(source_id, set()).add(pid)
        bucket = by_pid.setdefault(pid, {"inserted": set(), "base": set()})
        if inserted_into:
            bucket["inserted"].add(source_id)
            if row["unit_type"] == "section" and row["unit_number"]:
                inserted_sections.add((source_id, inserted_into, row["unit_number"], pid))
        else:
            bucket["base"].add(source_id)

    for pid, bucket in sorted(by_pid.items()):
        for amendment_source_id in sorted(bucket["inserted"]):
            for base_source_id in sorted(bucket["base"] - {amendment_source_id}):
                print(
                    f"[SUPERSESSION-CANDIDATE] {pid}: inserted by {amendment_source_id} "
                    f"collides with indexed base provision in {base_source_id}"
                )

    for amendment_source_id, target, number, pid in sorted(inserted_sections):
        for base_pid in sorted(target_pids.get(target, set())):
            pathless = f"{target}:section:{number}".lower()
            if base_pid != pathless and base_pid.endswith(f":section:{number}".lower()):
                print(
                    f"[WARN] {amendment_source_id}: inserted path-less section id {pid} may not join "
                    f"path-scoped target section id {base_pid}"
                )


def _require_services() -> None:
    """Reindex still embeds + upserts, so fail loudly if the backends are down."""
    from app.runtime.health import ping_url, qdrant_ok

    if not qdrant_ok():
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
        sources = load_allowed_sources()
        splice_plan = build_splice_plan(conn)
        target_source_ids = _expanded_reindex_scope(conn, sources, doc_id, splice_plan) if doc_id else None
        for source in sources:
            row = _latest_version(conn, source.source_id)
            if row is None:
                print(f"[SKIP] {source.source_id} — no indexed version (run sync first)")
                continue
            if target_source_ids is not None and source.source_id not in target_source_ids:
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
                splice_plan=splice_plan,
            )
            parents = conn.execute(
                "SELECT COUNT(*) AS c FROM chunk_parents WHERE doc_id = ?", [row["doc_id"]]
            ).fetchone()["c"]
            conn.commit()
            print(f"[OK] {source.source_id} indexed {chunks} chunks, {parents} parents")
            results.append({"source_id": source.source_id, "chunks": chunks, "parents": parents})

        if doc_id is None:  # full reindex → check every override/resolution warning corpus-wide
            _warn_unmatched_overrides(conn)
            _warn_amendment_aggregate(conn)
            check_consolidation_coherence(conn, splice_plan)
    finally:
        conn.close()

    if doc_id and not results:
        print(f"[WARN] no source matched '{doc_id}'")
    return results


def _expanded_reindex_scope(conn, sources, doc_id: str | None, splice_plan) -> set[str]:
    requested: set[str] = set()
    for source in sources:
        row = _latest_version(conn, source.source_id)
        if row is not None and doc_id in (row["doc_id"], source.source_id):
            requested.add(source.source_id)

    expanded = set(requested)
    pair_messages: set[tuple[str, str]] = set()
    for base_source_id, splices in splice_plan.splices_by_base_doc.items():
        for splice in splices:
            pair = (base_source_id, splice.amendment_source_id)
            if base_source_id in requested and splice.amendment_source_id not in expanded:
                expanded.add(splice.amendment_source_id)
                pair_messages.add(pair)
            if splice.amendment_source_id in requested and base_source_id not in expanded:
                expanded.add(base_source_id)
                pair_messages.add(pair)

    for base_source_id, amendment_source_id in sorted(pair_messages):
        print(
            f"[consolidation] {base_source_id} <-> {amendment_source_id} "
            "reindexed together"
        )
    return expanded
