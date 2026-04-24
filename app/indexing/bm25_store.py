from pathlib import Path
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.schema import TextNode
from app.config import settings

def build_and_save(nodes: list[TextNode]) -> None:
	retriever = BM25Retriever.from_defaults(
			nodes=nodes,
			similarity_top_k=10
			)
	retriever.persist(settings.bm25_path)

def load() -> BM25Retriever | None:
	path = Path(settings.bm25_path)
	if not path.exists():
		return None
	return BM25Retriever.from_persist_dir(settings.bm25_path)
