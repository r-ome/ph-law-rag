import json
from datetime import datetime, timezone
from llama_index.core.schema import TextNode
from app.indexing.chunker import chunk_texts, extract_parents
from app.indexing.bm25_store import build_and_save
from app.indexing.embedder import embed_texts
from app.indexing.vector_store import (
	get_qdrant_client, ensure_collection,
	upsert_nodes, delete_by_doc_id, refresh_doc_payload
)

# Doc-level metadata fields that are NOT embedded into chunk text, so a manifest edit
# can be pushed into the derived stores by patching payload/metadata only — no re-embed.
TIER_A_FIELDS = ("status", "url", "tags", "category", "doc_type")
# Fields that change the embedded text or the chunk boundaries (title/official_number are
# baked into chunk text via chunker._with_source_context; structure drives chunk_texts).
# A change here requires re-chunk + re-embed, not a payload patch.
TIER_B_FIELDS = ("title", "official_number", "structure")

def index_document(
	conn,
	doc_id: str,
	text: str,
	source_metadata: dict,
	version_id: str
) -> int:
	client = get_qdrant_client()
	ensure_collection(client)

	# SQLite deletes are uncommitted here (they roll back with the transaction if
	# anything below raises — process_source commits only after this returns).
	conn.execute("DELETE FROM chunks WHERE doc_id = ?", [doc_id])
	conn.execute("DELETE FROM chunk_parents WHERE doc_id = ?", [doc_id])

	nodes = chunk_texts(text, source_metadata)

	texts = [node.text for node in nodes]
	vectors = embed_texts(texts)

	# Dual-store writes are NOT atomic (SQLite + Qdrant). Keep the Qdrant
	# delete+upsert here, adjacent and after chunk/embed, so the common failure
	# (chunk or embed raising) leaves Qdrant at its prior state — consistent with
	# the SQLite transaction that will roll back. A failure strictly between the
	# delete and upsert self-heals on the next sync (this same delete+upsert reruns
	# from the prior content_hash). Full atomicity would need an outbox/2PC.
	delete_by_doc_id(client, doc_id)
	upsert_nodes(client, nodes, vectors)

	now = datetime.now(timezone.utc).isoformat()
	for i, node in enumerate(nodes):
		chunk_id = node.node_id
		conn.execute(
			"""
				INSERT INTO chunks(
					chunk_id,
					doc_id,
					version_id,
					chunk_index,
					text,
					char_count,
					token_estimate,
					qdrant_id,
					metadata_json,
					created_at
				) VALUES (?,?,?,?,?,?,?,?,?,?);
			""",
			[
				chunk_id,
				doc_id,
				version_id,
				i,
				node.text,
				len(node.text),
				len(node.text) //4,
				chunk_id,
				json.dumps(node.metadata),
				now
			]
		)

	for p in extract_parents(text, source_metadata):
		conn.execute(
			"""
				INSERT OR REPLACE INTO chunk_parents(
					parent_key, doc_id, source_id, title, url,
					unit_type, unit_label, structure_path, text, char_count, created_at
				) VALUES (?,?,?,?,?,?,?,?,?,?,?);
			""",
			[
				p["parent_key"], doc_id, p["source_id"], p["title"], p["url"],
				p["unit_type"], p["unit_label"], p["structure_path"],
				p["text"], p["char_count"], now,
			],
		)

	_rebuild_bm25(conn)

	return len(nodes)

def _rebuild_bm25(conn) -> None:
	# BM25 has no incremental update — always rebuild from the full chunks table.
	# Callers must have written any metadata/text changes to SQLite BEFORE calling this.
	all_rows = conn.execute("SELECT chunk_id, text, metadata_json FROM chunks").fetchall()
	all_nodes = [
		TextNode(id_=row["chunk_id"], text=row["text"], metadata=json.loads(row["metadata_json"]))
		for row in all_rows
	]
	build_and_save(all_nodes)

def refresh_document_metadata(
	conn,
	doc_id: str,
	source_metadata: dict,
	text: str,
	version_id: str,
) -> tuple[str, int]:
	"""Reconcile a doc's derived stores with manifest metadata when the legal text is
	unchanged. Returns (action, chunk_count):
	  - "skip":     no refreshable drift, nothing done.
	  - "meta":     Tier A in-place payload refresh, no re-embed.
	  - "reindex":  Tier B (embedded-text/boundary change) or missing chunks → re-chunk
	                under the EXISTING version_id (no new document_versions row).
	On Qdrant/embed failure this raises; the caller must count the source failed and must
	NOT commit (partial-store drift would otherwise persist as wrong retrieval state)."""
	rows = conn.execute(
		"SELECT chunk_id, metadata_json FROM chunks WHERE doc_id = ?", [doc_id]
	).fetchall()

	# Never indexed despite an existing version → re-index in place (Tier B path).
	if not rows:
		index_document(
			conn=conn, doc_id=doc_id, text=text,
			source_metadata=source_metadata, version_id=version_id,
		)
		count = conn.execute(
			"SELECT COUNT(*) AS n FROM chunks WHERE doc_id = ?", [doc_id]
		).fetchone()["n"]
		return ("reindex", count)

	stored = json.loads(rows[0]["metadata_json"])

	# A baked-field / boundary change can't be patched — re-chunk + re-embed in place.
	if any(stored.get(f) != source_metadata.get(f) for f in TIER_B_FIELDS):
		index_document(
			conn=conn, doc_id=doc_id, text=text,
			source_metadata=source_metadata, version_id=version_id,
		)
		count = conn.execute(
			"SELECT COUNT(*) AS n FROM chunks WHERE doc_id = ?", [doc_id]
		).fetchone()["n"]
		return ("reindex", count)

	changed = {
		f: source_metadata.get(f)
		for f in TIER_A_FIELDS
		if stored.get(f) != source_metadata.get(f)
	}
	if not changed:
		return ("skip", 0)

	# Tier A: external mutation first (mirrors index_document's Qdrant-before-SQLite order),
	# so a Qdrant failure leaves SQLite untouched and the caller rolls back cleanly.
	client = get_qdrant_client()
	refresh_doc_payload(client, doc_id, changed)

	# Merge changed doc-level fields into each chunk's metadata, preserving per-chunk keys
	# (is_structural, unit_label, structure_path, parent_key, part_index, ...).
	for row in rows:
		meta = json.loads(row["metadata_json"])
		meta.update(changed)
		conn.execute(
			"UPDATE chunks SET metadata_json = ? WHERE chunk_id = ?",
			[json.dumps(meta), row["chunk_id"]],
		)

	# chunk_parents carries only url at Tier A (title is Tier B → handled by re-index).
	if "url" in changed:
		conn.execute(
			"UPDATE chunk_parents SET url = ? WHERE doc_id = ?", [changed["url"], doc_id]
		)

	_rebuild_bm25(conn)
	return ("meta", len(rows))
