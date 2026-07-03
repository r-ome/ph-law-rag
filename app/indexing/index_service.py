import json
from datetime import datetime, timezone
from llama_index.core.schema import TextNode
from app.indexing.chunker import chunk_texts, extract_parents, provision_spans
from app.indexing.consolidation import (
	SplicePlan, consolidate, load_amendment_texts,
)
from app.indexing.bm25_store import build_and_save
from app.indexing.embedder import embed_texts
from app.indexing.vector_store import (
	get_qdrant_client, ensure_collection,
	upsert_nodes, delete_by_doc_id, refresh_doc_payload,
	set_chunk_payload,
)
from app.indexing.provision_status import load_provision_overrides, apply_overrides
from app.source_metadata import operability_action_for

# Doc-level metadata fields that are NOT embedded into chunk text, so a manifest edit
# can be pushed into the derived stores by patching payload/metadata only — no re-embed.
TIER_A_FIELDS = ("status", "url", "tags", "category", "doc_type")
# Fields that change the embedded text or the chunk boundaries (title/official_number are
# baked into chunk text via chunker._with_source_context; structure drives chunk_texts).
# A change here requires re-chunk + re-embed, not a payload patch.
TIER_B_FIELDS = ("title", "official_number", "structure", "amends", "amends_namespace")

def index_document(
	conn,
	doc_id: str,
	text: str,
	source_metadata: dict,
	version_id: str,
	splice_plan: SplicePlan | None = None,
) -> int:
	client = get_qdrant_client()
	ensure_collection(client)

	source_id = source_metadata.get("source_id")
	splices = splice_plan.splices_by_base_doc.get(source_id, ()) if splice_plan else ()
	if splices:
		text = consolidate(
			base_text=text,
			base_spans=provision_spans(text, source_metadata),
			splices=splices,
			amendment_texts=load_amendment_texts(conn, splices),
		)

	# SQLite deletes are uncommitted here (they roll back with the transaction if
	# anything below raises — process_source commits only after this returns).
	conn.execute("DELETE FROM chunks WHERE doc_id = ?", [doc_id])
	conn.execute("DELETE FROM chunk_parents WHERE doc_id = ?", [doc_id])

	nodes = chunk_texts(text, source_metadata)
	if source_metadata.get("amends"):
		report_amendment_indexing(conn, source_metadata, nodes)

	_stamp_consolidated_nodes(nodes, splices)

	# Stamp provision-level operability overrides onto matching chunks (whole-provision repeal/
	# reclassification). No-op for chunks without a matching provision_id. Sets operability_action
	# (the retrieval switch) + provision_status/basis; never the doc-level status.
	overrides = load_provision_overrides()
	for node in nodes:
		apply_overrides(node.metadata, overrides)
	_stamp_hidden_consolidated_insertions(
		nodes,
		set(splice_plan.hidden_keys_by_amendment.get(source_id, ())) if splice_plan else set(),
	)

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


def _stamp_consolidated_nodes(nodes: list[TextNode], splices: tuple) -> None:
	by_key = {splice.key: splice for splice in splices}
	if not by_key:
		return
	for node in nodes:
		splice = by_key.get(node.metadata.get("provision_id"))
		if splice is None:
			continue
		node.metadata.update({
			"consolidated": 1,
			"amended_by": [splice.amendment_source_id],
			"amendment_official_number": splice.amendment_official_number,
			"amendment_approval_date": splice.amendment_approval_date,
			"consolidation_basis": "single_full_restatement",
		})


def _stamp_hidden_consolidated_insertions(nodes: list[TextNode], hidden_keys: set[str]) -> None:
	if not hidden_keys:
		return
	for node in nodes:
		if not node.metadata.get("inserted_into"):
			continue
		if node.metadata.get("provision_id") not in hidden_keys:
			continue
		node.metadata["operability_action"] = "hide"
		node.metadata["provision_status"] = "consolidated"
		node.metadata["operability_basis"] = "consolidated"


def report_amendment_indexing(conn, source_metadata: dict, nodes: list[TextNode]) -> None:
	source_id = source_metadata.get("source_id")
	inserted = [n for n in nodes if n.metadata.get("inserted_into")]
	if not inserted:
		print(f"[WARN] {source_id}: amendment mode parsed 0 inserted provisions.")

	_warn_amendment_namespace(source_metadata)
	_warn_amendment_collisions(conn, source_id, inserted)
	_warn_path_scoped_inserted_sections(conn, source_id, inserted)


def _warn_amendment_namespace(source_metadata: dict) -> None:
	try:
		from app.config import load_allowed_sources
		enabled_source_ids = {s.source_id for s in load_allowed_sources()}
	except Exception as e:
		print(f"[WARN] {source_metadata.get('source_id')}: could not validate amendment namespace: {e}")
		return

	amends = source_metadata.get("amends") or []
	if len(amends) == 1:
		target = amends[0]
	elif source_metadata.get("amends_namespace"):
		target = source_metadata["amends_namespace"]
	else:
		target = source_metadata.get("source_id")
	if target not in enabled_source_ids:
		print(f"[WARN] {source_metadata.get('source_id')}: amendment target namespace {target!r} is not an enabled source_id")


def _warn_amendment_collisions(conn, amendment_source_id: str | None, inserted: list[TextNode]) -> None:
	for pid in sorted({n.metadata.get("provision_id") for n in inserted if n.metadata.get("provision_id")}):
		rows = conn.execute(
			"""
				SELECT DISTINCT json_extract(metadata_json, '$.source_id') AS source_id
				FROM chunks
				WHERE json_extract(metadata_json, '$.provision_id') = ?
				  AND json_extract(metadata_json, '$.source_id') != ?
			""",
			[pid, amendment_source_id],
		).fetchall()
		for row in rows:
			base_source_id = row["source_id"]
			if base_source_id:
				print(
					f"[SUPERSESSION-CANDIDATE] {pid}: inserted by {amendment_source_id} "
					f"collides with indexed base provision in {base_source_id}"
				)


def _warn_path_scoped_inserted_sections(conn, amendment_source_id: str | None, inserted: list[TextNode]) -> None:
	seen: set[tuple[str, str, str]] = set()
	for node in inserted:
		meta = node.metadata
		if meta.get("unit_type") != "section":
			continue
		target = meta.get("inserted_into")
		number = meta.get("unit_number")
		pid = meta.get("provision_id")
		if not target or not number or not pid:
			continue
		rows = conn.execute(
			"""
				SELECT DISTINCT json_extract(metadata_json, '$.provision_id') AS provision_id
				FROM chunks
				WHERE json_extract(metadata_json, '$.source_id') = ?
				  AND json_extract(metadata_json, '$.provision_id') LIKE ?
				  AND json_extract(metadata_json, '$.provision_id') != ?
			""",
			[target, f"{target}:%:section:{number}".lower(), pid],
		).fetchall()
		for row in rows:
			base_pid = row["provision_id"]
			if not base_pid:
				continue
			key = (pid, target, base_pid)
			if key in seen:
				continue
			seen.add(key)
			print(
				f"[WARN] {amendment_source_id}: inserted path-less section id {pid} may not join "
				f"path-scoped target section id {base_pid}"
			)

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

	# operability_action is DERIVED from doc status, so a status change must re-derive it — but
	# PER CHUNK, because provision overrides pin some chunks to a different action than the doc
	# default. It is therefore NOT carried in the doc-wide `changed` dict (which would clobber
	# overridden chunks); recompute per row and write per chunk. Override-file edits are not
	# detected here (unchanged content + unchanged manifest = skip) — they require `raglab reindex`.
	recompute_action = "status" in changed
	overrides = load_provision_overrides() if recompute_action else {}

	# Merge changed doc-level fields into each chunk's metadata, preserving per-chunk keys
	# (is_structural, unit_label, structure_path, parent_key, part_index, provision_id, ...).
	for row in rows:
		meta = json.loads(row["metadata_json"])
		meta.update(changed)
		if recompute_action:
			meta["operability_action"] = operability_action_for(meta.get("status"))
			apply_overrides(meta, overrides)  # re-pin overridden provisions
			set_chunk_payload(client, row["chunk_id"], {"operability_action": meta["operability_action"]})
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
