from qdrant_client import QdrantClient
from qdrant_client.models import (
	Distance, VectorParams, PointStruct,
	Filter, FieldCondition, MatchValue,
	PayloadSchemaType,
)
from llama_index.core.schema import TextNode
from app.config import settings

# Denylist (fail-open): keep only in-force law. "unknown" is intentionally allowed
# through; "not_yet_effective" is excluded so future law isn't surfaced as current authority.
# NON_OPERATIVE no longer feeds the retrieval filter directly — it derives operability_action
# (the sole retrieval switch). Retrieval filters on operability_action == "hide".
NON_OPERATIVE = ["superseded", "repealed", "not_yet_effective"]
FILTER_PAYLOAD_FIELDS = ("doc_id", "source_id", "operability_action")


def operability_action_for(status: str | None) -> str:
	"""Default retrieval action derived from a chunk's DOCUMENT status. Provision-level
	overrides may replace this per chunk; the filter reads operability_action, never status."""
	return "hide" if status in NON_OPERATIVE else "show"

def get_qdrant_client() -> QdrantClient:
	key = settings.qdrant_api_key.get_secret_value()
	return QdrantClient(url=settings.qdrant_url, api_key=key or None)

def _vector_size(collection_info) -> int:
	vectors = collection_info.config.params.vectors
	return vectors.size if hasattr(vectors, "size") else next(iter(vectors.values())).size

def _ensure_filter_payload_indexes(client: QdrantClient) -> None:
	collection_info = client.get_collection(settings.qdrant_collection)
	payload_schema = collection_info.payload_schema or {}
	for field in FILTER_PAYLOAD_FIELDS:
		if field in payload_schema:
			continue
		client.create_payload_index(
			collection_name=settings.qdrant_collection,
			field_name=field,
			field_schema=PayloadSchemaType.KEYWORD,
			wait=True,
		)

def ensure_collection(client: QdrantClient) -> None:
	existing = [c.name for c in client.get_collections().collections]
	if settings.qdrant_collection not in existing:
		client.create_collection(
			collection_name=settings.qdrant_collection,
			vectors_config=VectorParams(
				size=settings.embedding_dim,
				distance=Distance.COSINE,
			),
		)
	else:
		collection_info = client.get_collection(settings.qdrant_collection)
		current = _vector_size(collection_info)
		if current != settings.embedding_dim:
			raise RuntimeError(
				f"Collection '{settings.qdrant_collection}' is currently {current}-dim, "
				f"EMBEDDING_DIM={settings.embedding_dim}. Use a new QDRANT_COLLECTION "
				f"or delete the existing one before reindex."
			)

	_ensure_filter_payload_indexes(client)

def upsert_nodes(
	client: QdrantClient,
	nodes: list[TextNode],
	vectors: list[list[float]],
	batch_size: int = 128,
) -> None:
	points = [
		PointStruct(
			id=node.node_id,
			vector=vector,
			payload={**node.metadata, "text": node.text},
		)
		for node, vector in zip(nodes, vectors)
	]
	for i in range(0, len(points), batch_size):
		client.upsert(
			collection_name=settings.qdrant_collection,
			points=points[i:i + batch_size],
		)

def delete_by_doc_id(client: QdrantClient, doc_id: str) -> None:
	client.delete(
		collection_name=settings.qdrant_collection,
		points_selector=Filter(
			must=[FieldCondition(
				key="doc_id",
				match=MatchValue(value=doc_id)
			)]
		)
	)
			
def refresh_doc_payload(client: QdrantClient, doc_id: str, payload_fields: dict) -> None:
	# In-place metadata refresh, NO re-embed: set only the changed payload keys on every
	# point for this doc, selected by the doc_id filter. set_payload merges (other keys,
	# including the baked "text" and the vector, are untouched). Only safe for fields not
	# embedded into chunk text — title/official_number must go through full re-index instead.
	client.set_payload(
		collection_name=settings.qdrant_collection,
		payload=payload_fields,
		points=Filter(
			must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
		),
		wait=True,
	)

def set_chunk_payload(client: QdrantClient, chunk_id: str, payload_fields: dict) -> None:
	# Per-point payload merge (no re-embed), selected by point id. Used by the refresh path for
	# fields that are NOT doc-uniform — e.g. operability_action, which differs on provision-
	# overridden chunks and so cannot go through the doc-wide refresh_doc_payload.
	client.set_payload(
		collection_name=settings.qdrant_collection,
		payload=payload_fields,
		points=[chunk_id],
		wait=True,
	)

def operative_filter(source_id: str | None = None) -> Filter | None:
	must = (
		[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
		if source_id else []
	)
	# fail-open: only chunks explicitly marked operability_action="hide" are excluded
	# (chunks lacking the field — e.g. pre-reindex — are kept). operability_action is the sole
	# retrieval switch; it is derived from doc status by default and overridden per provision.
	must_not = (
		[FieldCondition(key="operability_action", match=MatchValue(value="hide"))]
		if settings.retrieval_operative_only else []
	)

	if not must and not must_not:
		return None
	return Filter(must=must or None, must_not=must_not or None)

def query(client, vector: list[float], top_k: int, query_filter: Filter | None = None):
	return client.query_points(
		collection_name=settings.qdrant_collection,
		query=vector,
		limit=top_k,
		with_payload=True,
		query_filter=query_filter
	).points
