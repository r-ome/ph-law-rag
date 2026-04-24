from qdrant_client import QdrantClient
from qdrant_client.models import (
	Distance, VectorParams, PointStruct,
	Filter, FieldCondition, MatchValue
)
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
			
