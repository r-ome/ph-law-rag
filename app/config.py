import yaml
from datetime import date
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, ConfigDict, SecretStr

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
	sparse_overfetch_k: int = 100  # BM25 candidate pool to filter before taking sparse_top_k operative hits
	rerank_top_n: int = 5
	rerank_score_margin: float = 3.0  # relative trim: drop chunks scoring more than this below the top reranked chunk (cross-encoder logits aren't comparable across queries, so an absolute floor can't gate). Abstention is the LLM's job, not the reranker's.
	max_distance: float = 0.5
	min_chunks_for_answer: int = 1
	retrieval_operative_only: bool = True
	# llm_model: str = "deepseek-r1:8b"
	llm_model: str = "mistral"
	# llm_model: str = "qwen3:4b"
	reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
	debug: bool = False
	eval_dataset_path: str = "data/eval_dataset.jsonl"
	eval_results_dir: str = "data/eval_results"
	ragas_llm_model: str = "claude-haiku-4-5-20251001"
	ragas_embedding_model: str = "nomic-embed-text"
	anthropic_api_key: SecretStr = SecretStr("")
	api_base_url: str = "http://localhost:8000"
	edge_expansion_enabled: bool = True
	edge_hop_top_k: int = 3
	answerability_gate_enabled: bool = False  # off until the revised gate beats baseline on the full 70
	answerability_gate_model: str = "mistral"  # gate pinned to mistral even when A/B-ing another generator

Category = Literal[
	"constitutional_law", "statute", "presidential_issuance",
	"administrative_regulation", "court_material",
]
Status = Literal["operative", "superseded", "repealed", "not_yet_effective", "unknown"]
Availability = Literal["available", "unavailable", "restricted"]
SourceIndex = Literal["sc_elibrary", "sc_website", "onar", "pco", "lawphil"]
Structure = Literal["hierarchical", "prose", "auto"]
FileFormat = Literal["html", "pdf"]

class SourceConfig(BaseModel):
	model_config = ConfigDict(extra="forbid")

	# Identity & fetch
	source_id: str
	enabled: bool
	file_format: FileFormat
	url: str
	availability: Availability = "available"

	# Classification
	category: Category
	doc_type: str
	tags: list[str] = []

	# Bibliographic
	title: str
	official_number: str | None = None
	approval_date: date | None = None
	effectivity_date: date | None = None

	# Temporal status (in-force state only)
	status: Status

	# Forward edges only — inverse is derived at load
	amends: list[str] = []
	repeals: list[str] = []
	supersedes: list[str] = []
	implements: list[str] = []

	# Provenance / source-of-truth
	source_index: SourceIndex
	source_record_id: str | None = None

	# Chunker hint (ADR-007)
	structure: Structure = "auto"

	notes: str | None = None

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
	
