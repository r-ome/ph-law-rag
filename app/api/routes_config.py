from fastapi import APIRouter
from pydantic import BaseModel

from app.config import config_view

router = APIRouter(prefix="/config", tags=["config"])


class ConfigView(BaseModel):
    profile: str
    policy_overrides: dict[str, object]
    env_ignored: dict[str, object]
    embedding_backend: str
    embedding_model: str | None = None
    embedding_dim: int | None = None
    embedding_query_instruction: str | None = None
    llm_model: str
    strong_model: str | None = None
    escalate_intents: list[str] = []
    escalate_on_partial_evidence: bool
    generator_backend: str
    reranker_backend: str
    qdrant_collection: str
    qdrant_url: str
    ollama_base_url: str
    chunk_size: int
    chunk_overlap: int
    min_chunks_for_answer: int
    evidence_gate: str
    evidence_judge_model: str
    corrective_retrieval_enabled: bool
    max_conversation_turns: int
    router_enabled: bool
    edge_expansion_enabled: bool
    answerability_gate_enabled: bool
    enable_query_rewriting: bool
    faithfulness_selfcheck_enabled: bool
    later_enacted_preference_enabled: bool
    aws_region: str


@router.get("", response_model=ConfigView, summary="Curated, secret-free runtime config")
def config() -> ConfigView:
    return ConfigView(**config_view())
