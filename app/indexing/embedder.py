from app.config import settings

_embed_model = None
_embed_model_key = None

def get_embed_model():
	global _embed_model, _embed_model_key
	key = (settings.embedding_backend, settings.embedding_model, settings.ollama_base_url, settings.aws_region)
	if _embed_model is not None and _embed_model_key == key:
		return _embed_model
	if settings.embedding_backend == "bedrock":
		from llama_index.embeddings.bedrock import BedrockEmbedding
		from botocore.config import Config
		_embed_model = BedrockEmbedding(
			model_name=settings.embedding_model,
			region_name=settings.aws_region,
			botocore_config=Config(
				retries={"max_attempts": 10, "mode": "adaptive"}
			)
			)
		_embed_model_key = key
		return _embed_model
	from llama_index.embeddings.ollama import OllamaEmbedding
	_embed_model = OllamaEmbedding(
		model_name=settings.embedding_model,
		base_url=settings.ollama_base_url
	)
	_embed_model_key = key
	return _embed_model

def embed_texts(texts: list[str]) -> list[list[float]]:
	model = get_embed_model()
	return model.get_text_embedding_batch(texts)


def release_embedding_model() -> dict[str, object]:
	global _embed_model, _embed_model_key
	result = {"attempted": True, "result": False, "warning": None}
	_embed_model = None
	_embed_model_key = None
	# OllamaEmbedding has no keep_alive option. Keep this best-effort and local;
	# callers may use the process boundary when an unload cannot be requested.
	try:
		import httpx
		if settings.embedding_backend == "ollama":
			response = httpx.post(
				f"{settings.ollama_base_url}/api/generate",
				json={"model": settings.embedding_model, "prompt": "", "keep_alive": 0, "stream": False},
				timeout=5,
			)
			response.raise_for_status()
		result["result"] = True
	except Exception as exc:
		result["warning"] = str(exc)
	return result
