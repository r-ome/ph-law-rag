from qdrant_client import QdrantClient
from qdrant_client.models import (
	Distance, VectorParams, PointStruct,
	Filter, FieldCondition, MatchValue, MatchAny
)

# Denylist (fail-open): keep only in-force law. "unknown" is intentionally allowed
# through; "not_yet_effective" is excluded so future law isn't surfaced as current authority.
NON_OPERATIVE = ["superseded", "repealed", "not_yet_effective"]
from llama_index.core.schema import TextNode
from app.config import settings

def get_qdrant_client() -> QdrantClient:
	return QdrantClient(url=settings.qdrant_url)

def ensure_collection(client: QdrantClient) -> None:
	existing = [c.name for c in client.get_collections().collections]
	if settings.qdrant_collection not in existing:
		client.create_collection(
			collection_name=settings.qdrant_collection,
			vectors_config=VectorParams(size=768, distance=Distance.COSINE)
		)

def upsert_nodes(
	client: QdrantClient,
	nodes: list[TextNode],
	vectors: list[list[float]]
) -> None:
	points = [
		PointStruct(
			id=node.node_id,
			vector=vector,
			payload={**node.metadata, "text": node.text}
		)
		for node, vector in zip(nodes, vectors)
	]
	
	client.upsert(
		collection_name=settings.qdrant_collection,
		points=points
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
			
def operative_filter(source_id: str | None = None) -> Filter | None:
	must = (
		[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
		if source_id else []
	)
	# fail-open: only chunks explicitly marked non-operative are excluded
	must_not = (
		[FieldCondition(key="status", match=MatchAny(any=NON_OPERATIVE))]
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
