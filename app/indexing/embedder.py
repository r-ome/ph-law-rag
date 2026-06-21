from app.config import settings

def get_embed_model():
	if settings.embedding_backend == "bedrock":
		from llama_index.embeddings.bedrock import BedrockEmbedding
		from botocore.config import Config
		return BedrockEmbedding(
			model_name=settings.embedding_model,
			region_name=settings.aws_region,
			botocore_config=Config(
				retries={"max_attempts": 10, "mode": "adaptive"}
			)
		)
	from llama_index.embeddings.ollama import OllamaEmbedding
	return OllamaEmbedding(
		model_name=settings.embedding_model,
		base_url=settings.ollama_base_url
	)

def embed_texts(texts: list[str]) -> list[list[float]]:
	model = get_embed_model()
	return model.get_text_embedding_batch(texts)
