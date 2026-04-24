import json
from datetime import datetime, timezone
from llama_index.core.schema import TextNode
from app.indexing.chunker import chunk_texts
from app.indexing.bm25_store import build_and_save
from app.indexing.embedder import embed_texts
from app.indexing.vector_store import (
	get_qdrant_client, ensure_collection,
	upsert_nodes, delete_by_doc_id
)

def index_document(
	conn,
	doc_id: str,
	text: str,
	source_metadata: dict,
	version_id: str
) -> int:
	client = get_qdrant_client()
	ensure_collection(client)

	delete_by_doc_id(client, doc_id)
	conn.execute("DELETE FROM chunks WHERE doc_id = ?", [doc_id])

	nodes = chunk_texts(text, source_metadata)

	texts = [node.text for node in nodes]
	vectors = embed_texts(texts)

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
				json.dumps(source_metadata),
				now
			]
		)

	all_rows = conn.execute("SELECT chunk_id, text, metadata_json FROM chunks").fetchall()
	all_nodes = [
		TextNode(id_=row["chunk_id"],text=row["text"], metadata=json.loads(row["metadata_json"]))
		for row in all_rows
	]
	build_and_save(all_nodes)

	return len(nodes)
