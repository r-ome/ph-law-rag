import os
import yaml
from datetime import date
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

_EMBEDDING_BACKEND_DEFAULTS = {
	"ollama": {"model": "qwen3-embedding:0.6b", "dim": 1024},
	"bedrock": {"model": "amazon.titan-embed-text-v2:0", "dim": 1024},
}

_KNOWN_EMBEDDING_DIMS = {
	"nomic-embed-text": 768,
	"qwen3-embedding:0.6b": 1024,
	"amazon.titan-embed-text-v2:0": 1024,
	"amazon.titan-embed-text-v2:0:8k": 1024,
}

_OLLAMA_EMBEDDING_MODELS = {"nomic-embed-text", "qwen3-embedding:0.6b"}
_BEDROCK_EMBEDDING_MODELS = {
	"amazon.titan-embed-text-v2:0",
	"amazon.titan-embed-text-v2:0:8k",
}

class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=os.getenv("RAGLAB_ENV_FILE", ".env"))
	source_config_path: str = "sources/ph_law_sources.yaml"
	db_path: str = "data/sqlite/ph-law-rag.db"
	raw_data_dir: str = "data/raw"
	normalized_data_dir: str = "data/normalized"
	request_timeout: int = 30
	raglab_profile: str = "local"
	# Qdrant Config
	qdrant_collection: str = "ph_law_qwen06"
	qdrant_url: str = "http://localhost:6333"
	qdrant_api_key: SecretStr = SecretStr("")

	bm25_path: str = "data/bm25"
	chunk_size: int = 256
	chunk_overlap: int = 32
	# Embeddings Configs
	embedding_backend: Literal["ollama", "bedrock"] = "ollama"
	embedding_model: str | None = None
	embedding_dim: int | None = None
	embedding_query_instruction: str | None = (
		"Given a Philippine law question, retrieve the statutory "
		"provisions and jurisprudence that answer it."
	)

	ollama_base_url: str = "http://localhost:11434"
	dense_top_k: int = 30
	sparse_top_k: int = 10
	sparse_overfetch_k: int = 100  # BM25 candidate pool to filter before taking sparse_top_k operative hits
	rerank_top_n: int = 8
	rerank_score_margin: float = 6.0  # relative trim: drop chunks scoring more than this below the top reranked chunk (cross-encoder logits aren't comparable across queries, so an absolute floor can't gate). Abstention is the LLM's job, not the reranker's. Widened from 5/3.0 (2026-06-17): retrieve-trace evidence showed top8/margin6 re-gathers enumeration fragments lost to provision-aware chunking (e.g. §21(1)/(4)/(8)) without adding net noise.
	max_distance: float = 0.5
	min_chunks_for_answer: int = 1
	retrieval_operative_only: bool = True
	# Post-rerank parent expansion: once >= min_children leaves of one section survive the cutoff,
	# swap them for the whole parent section so generation sees the full enumeration/list (the
	# within-doc list-span regression from provision-aware chunking). No partial truncation — if the
	# parent would blow max_chars, fall back to the leaves exactly as retrieved.
	parent_expansion_enabled: bool = True   # default-on 2026-06-18: clean per-changed-row win (see NOTE below)
	parent_expansion_min_children: int = 2
	parent_expansion_max_chars: int = 8000
	# Recovery of adjacent enumeration leaves after parent expansion. Default-on 2026-07-16
	# (ADR-026): matched A/B on 131 rows — exact-leaf coverage .577→.615, context recall +.024,
	# precision flat, faithfulness delta noise-sized, false abstentions 7→5, additive-only.
	sibling_expansion_enabled: bool = True
	sibling_expansion_radius: int = 1
	sibling_expansion_max_chars: int = 3000
	sibling_expansion_max_tokens: int = 750
	# Operative-law preference: post-rerank, downrank a superseded provision below its operative
	# (amending) replacement when BOTH are retrieved. Query-time only, reorder-only (no drop, no
	# re-index). Map is provision-level retrieval policy, loaded from provision_supersession_path.
	prefer_operative_enabled: bool = False
	provision_supersession_path: str = "sources/provision_supersession.yaml"
	# Provision-level operability overrides (whole-provision repeal/reclassification). Applied at
	# INDEX time: stamps operability_action onto matching provision chunks so retrieval suppresses
	# dead base provisions (e.g. RPC Art 335 -> RA 8353 Art 266-A) without needing the amendment
	# labeled. Distinct from provision_supersession (a query-time reorder). Edits require reindex.
	provision_status_path: str = "sources/provision_status.yaml"
	# NOTE on judging this: parent expansion only changes context on the minority of questions
	# whose section was fragmented (~11/70 in eval). Judge it on CHANGED-CONTEXT rows
	# (recall +0.136, faithfulness +0.012, precision −0.014 on those), NOT whole-run aggregate
	# faithfulness — the untouched majority adds generator noise that swamps the signal.
	# Local generator default graduated with the 131-row Qwen baseline (ADR-025).
	llm_model: str = "gemma4:e4b"
	# Intent router (R4): Haiku classifier in front of non-greeting queries,
	# mapping intent -> strategy preset. Off by default so local surfaces stay
	# routerless; cloud/demo surfaces opt in via router_enabled=true.
	router_enabled: bool = False
	router_model: str = "claude-haiku-4-5"
	reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
	# Selector backend default (graduated 2026-07-07, ADR-021): Bedrock Rerank API,
	# serverless per-call. Matched qwen3 retrieval quality in the judged A/B (prec +.023,
	# faith/recall within noise) at ~0.8s/query vs qwen3's ~6.5s MPS. Scores are
	# uncalibrated relevance floats — ordering only, nothing like qwen3's P(yes)
	# probabilities; takes plain top-8, rerank_score_margin does not apply.
	# CAVEAT: amazon.rerank-v1 is quota-capped at 2 calls/min (non-adjustable) — calls are
	# paced 31s apart, so serving surfaces pin "minilm" instead (compose/cloud/infra).
	# "qwen3" (prior default, ADR-016) is kept as a research arm; same top-8 semantics,
	# [0,1] P(yes) scores, needs MPS + empty_cache per call.
	reranker_backend: Literal["minilm", "qwen3", "bedrock"] = "bedrock"
	qwen3_reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
	bedrock_rerank_model: str = "amazon.rerank-v1:0"
	# Rerank models are not served in us-east-1; only the rerank client points here —
	# the rest of the stack stays on aws_region.
	bedrock_rerank_region: str = "us-west-2"
	consolidated_dedup_enabled: bool = True
	debug: bool = False
	log_dir: str = "data/logs"
	log_level: str = "INFO"
	log_to_file: bool = True
	log_max_bytes: int = 10_000_000
	log_backup_count: int = 5
	trace_logging_enabled: bool = True
	trace_max_text_preview: int = 200
	eval_dataset_path: str = "data/eval_dataset.jsonl"
	eval_retrieval_targets_path: str = "data/eval_retrieval_targets.jsonl"
	eval_results_dir: str = "data/eval_results"
	legal_query_rewrite_model: str = "claude-haiku-4-5"
	legal_query_rewrite_timeout_seconds: float = 15.0
	legal_query_rewrite_cache_dir: str = "data/eval_results/legal_rewrite_cache"
	eval_run_label: str = ""  # optional A/B tag baked into eval output filenames
	ragas_score_cache_path: str = "data/eval_results/ragas_score_cache.sqlite"
	ragas_judge_backend: str = "anthropic"  # anthropic | openai — selects the RAGAS judge LLM
	ragas_llm_model: str = "claude-haiku-4-5-20251001"  # judge model when ragas_judge_backend=anthropic
	ragas_openai_model: str = "gpt-5-mini"  # judge model when ragas_judge_backend=openai
	ragas_embedding_model: str = "nomic-embed-text"
	anthropic_api_key: SecretStr = SecretStr("")
	openai_api_key: SecretStr = SecretStr("")
	edge_expansion_enabled: bool = True
	edge_hop_top_k: int = 3
	answerability_gate_enabled: bool = False  # off until the revised gate beats baseline on the full 70
	answerability_gate_model: str = "mistral"  # gate pinned to mistral even when A/B-ing another generator
	crag_judge_model: str = "claude-haiku-4-5"  # CRAG facet-judge; override (e.g. gemma4:e4b) to run the gatekeeper locally
	query_decomposition_enabled: bool = False
	query_planner_model: str = "mistral"
	query_planner_max_subqueries: int = 3
	subquery_packaging_enabled: bool = False  # per-subquery rerank + reserved slots (isolates the rerank-neck bug)
	subquery_reserve_n: int = 2  # top chunks reserved per subquery before round-robin merge
	# Conversation context (M8): follow-ups are rewritten to standalone queries before retrieval.
	max_conversation_turns: int = 5       # history window passed to the rewriter
	enable_query_rewriting: bool = True   # toggle rewriting off for debugging
	# Faithfulness self-check: optional 2nd local pass that audits the draft answer against the
	# retrieved context and deletes unsupported claims. Targets the generator groundedness gap
	# (the original Mistral experiment drifted from context; cloud didn't). Off by default.
	faithfulness_selfcheck_enabled: bool = False
	# Later-enacted-text preference: extra system-prompt rule telling the generator that when
	# context passages conflict (penalties/ages/thresholds, or a provision one passage shows was
	# replaced by a later law), the later-enacted text controls. Targets residual old-law
	# following that retrieval-side fixes (operability hide, consolidation) can't reach — e.g.
	# stale cross-references in operative law (RA 7610 §5(b) still citing repealed Art 335).
	# Off by default; A/B'd 2026-07-03 before any default flip.
	later_enacted_preference_enabled: bool = False
	# AWS config
	aws_region: str = "us-east-1"

	@model_validator(mode="after")
	def resolve_embedding_config(self):
		defaults = _EMBEDDING_BACKEND_DEFAULTS[self.embedding_backend]

		if self.embedding_model is None:
			self.embedding_model = defaults["model"]

		if self.embedding_backend == "bedrock" and self.embedding_model in _OLLAMA_EMBEDDING_MODELS:
			raise ValueError(
				"embedding_backend=bedrock cannot use an Ollama embedding model "
				f"({self.embedding_model!r}). Unset EMBEDDING_MODEL to use the "
				f"backend default {defaults['model']!r}, or set a Bedrock model "
				"with the correct EMBEDDING_DIM."
			)
		if self.embedding_backend == "ollama" and self.embedding_model in _BEDROCK_EMBEDDING_MODELS:
			raise ValueError(
				"embedding_backend=ollama cannot use a Bedrock embedding model "
				f"({self.embedding_model!r}). Unset EMBEDDING_MODEL to use the "
				f"backend default {defaults['model']!r}."
			)

		expected_dim = _KNOWN_EMBEDDING_DIMS.get(self.embedding_model)
		if self.embedding_dim is None:
			if expected_dim is None:
				raise ValueError(
					"embedding_dim must be set when embedding_model is not one "
					"of the known defaults."
				)
			self.embedding_dim = expected_dim

		if expected_dim is not None and self.embedding_dim != expected_dim:
			raise ValueError(
				f"embedding_model={self.embedding_model!r} expects "
				f"embedding_dim={expected_dim}, got {self.embedding_dim}."
			)

		return self


Category = Literal[
	"constitutional_law", "statute", "presidential_issuance",
	"administrative_regulation", "court_material",
]
Status = Literal["operative", "superseded", "repealed", "not_yet_effective", "unknown"]
Availability = Literal["available", "unavailable", "restricted"]
SourceIndex = Literal["sc_elibrary", "sc_website", "onar", "pco", "lawphil"]
Structure = Literal["hierarchical", "prose", "auto"]
FileFormat = Literal["html", "pdf"]
Extractor = Literal["auto", "bs4"]

class SourceConfig(BaseModel):
	model_config = ConfigDict(extra="forbid")

	# Identity & fetch
	source_id: str
	enabled: bool
	file_format: FileFormat
	url: str
	availability: Availability = "available"
	# HTML extraction override for pages where trafilatura drops law text (see parser.parse_html)
	extractor: Extractor = "auto"

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
	amends_namespace: str | None = None
	repeals: list[str] = []
	supersedes: list[str] = []
	implements: list[str] = []

	# Provenance / source-of-truth
	source_index: SourceIndex
	source_record_id: str | None = None

	# Chunker hint (ADR-007)
	structure: Structure = "auto"

	notes: str | None = None

	@model_validator(mode="after")
	def validate_amends_namespace(self):
		if self.amends_namespace is not None:
			if not self.amends_namespace:
				raise ValueError("amends_namespace must be non-empty when set")
			if self.amends_namespace not in self.amends:
				raise ValueError("amends_namespace must be one of amends")
		return self

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

def config_view() -> dict:
	"""Curated, secret-free config for the dashboard."""
	from app.pipeline.policy import resolve_policy

	resolution = resolve_policy()
	policy = resolution.policy
	return {
		"profile": policy.name,
		"policy_overrides": resolution.policy_overrides,
		"env_ignored": resolution.env_ignored,
		"embedding_backend": settings.embedding_backend,
		"embedding_model": settings.embedding_model,
		"embedding_dim": settings.embedding_dim,
		"embedding_query_instruction": settings.embedding_query_instruction,
		"llm_model": policy.generator_model,
		"strong_model": policy.strong_model,
		"escalate_intents": sorted(policy.escalate_intents),
		"escalate_on_partial_evidence": policy.escalate_on_partial_evidence,
		"generator_backend": "anthropic" if policy.generator_model.startswith("claude") else "ollama",
		"reranker_backend": settings.reranker_backend,
		"qdrant_collection": settings.qdrant_collection,
		"qdrant_url": settings.qdrant_url,
		"ollama_base_url": settings.ollama_base_url,
		"chunk_size": settings.chunk_size,
		"chunk_overlap": settings.chunk_overlap,
		"min_chunks_for_answer": policy.min_chunks_for_answer,
		"evidence_gate": policy.evidence_gate,
		"evidence_judge_model": policy.evidence_judge_model,
		"corrective_retrieval_enabled": policy.corrective_retrieval_enabled,
		"max_conversation_turns": settings.max_conversation_turns,
		"router_enabled": policy.router_enabled,
		"edge_expansion_enabled": policy.retrieval_defaults.edge_expansion_enabled,
		"answerability_gate_enabled": policy.evidence_gate == "answerability",
		"enable_query_rewriting": policy.query_rewriting_enabled,
		"faithfulness_selfcheck_enabled": policy.selfcheck_enabled,
		"later_enacted_preference_enabled": policy.later_enacted_preference_enabled,
		"aws_region": settings.aws_region,
	}
	
