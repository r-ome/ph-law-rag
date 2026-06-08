import yaml
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, SecretStr

class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=".env")
	source_config_path: str = "sources/ph_law_sources.yaml"
	db_path: str = "data/sqlite/ph-law-rag.db"
	raw_data_dir: str = "data/raw"
	normalized_data_dir: str = "data/normalized"
	request_timeout: int = 30
	qdrant_collection: str = "ph_law"
	qdrant_url: str = "http://localhost:6333"
	bm25_path: str = "data/bm25"
	chunk_size: int = 256
	chunk_overlap: int = 32
	embedding_model: str = "nomic-embed-text"
	ollama_base_url: str = "http://localhost:11434"
	dense_top_k: int = 10
	sparse_top_k: int = 10
	rerank_top_n: int = 5
	max_distance: float = 0.5
	min_chunks_for_answer: int = 2
	# llm_model: str = "deepseek-r1:8b"
	llm_model: str = "mistral"
	# llm_model: str = "qwen3:4b"
	reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
	debug: bool = False
	eval_dataset_path: str = "data/eval_dataset.jsonl"
	eval_results_dir: str = "data/eval_results"
	ragas_llm_model: str = "claude-sonnet-4-6"
	ragas_embedding_model: str = "nomic-embed-text"
	anthropic_api_key: SecretStr = SecretStr("")

class SourceConfig(BaseModel):
	source_id: str
	title: str
	url: str
	doc_type: str
	file_format: str
	category: str
	tags: list[str]
	enabled: bool

class SourceFile(BaseModel):
	sources: list[SourceConfig]

settings = Settings()

def load_allowed_sources() -> list[SourceConfig]:
	path = Path(settings.source_config_path)
	if not path.exists():
		raise FileNotFoundError(path)

	data = yaml.safe_load(path.read_text()) or {}
	parsed = SourceFile.model_validate(data)

	allowed: list[SourceConfig] = []
	for source in parsed.sources:
		if source.enabled:
			allowed.append(source)
	return allowed
	
