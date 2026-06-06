from typing import cast
from app.retriever.types import RetrievalResult
from app.indexing.bm25_store import load
from app.config import settings

def sparse_retriever(query_text: str) -> list[RetrievalResult]:
    retriever = load()
    if retriever is None:
        return cast(list[RetrievalResult], [])
    
    retriever.similarity_top_k = settings.sparse_top_k
    nodes = retriever.retrieve(query_text)
    
    return [
        RetrievalResult(
            chunk_id=n.node.node_id,
            text=n.node.get_content(),
            score=n.score if n.score is not None else 0.0,
            metadata=n.node.metadata
        )
        for n in nodes
    ]