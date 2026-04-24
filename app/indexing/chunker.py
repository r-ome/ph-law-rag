from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from app.config import settings

def chunk_texts(text: str, source_metadata: dict) -> list[TextNode]:
	splitter = SentenceSplitter(
		chunk_size=settings.chunk_size,
		chunk_overlap=settings.chunk_overlap
	)
	doc = Document(
		text=text,
		metadata=source_metadata
	)
	return splitter.get_nodes_from_documents([doc])
