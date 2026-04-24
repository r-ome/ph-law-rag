from llama_index.embeddings.ollama import OllamaEmbedding
from app.config import settings

def get_embed_model() -> OllamaEmbedding:
	return OllamaEmbedding(
		model_name=settings.embedding_model,
		base_url=settings.ollama_base_url
	)

def embed_texts(texts: list[str]) -> list[list[float]]:
	model = get_embed_model()
	return model.get_text_embedding_batch(texts)
