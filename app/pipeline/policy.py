from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

from app.config import settings
from app.retriever.strategy import RetrievalKnobs

EvidenceGate = Literal["min_chunks", "answerability", "crag"]
CorrectiveMode = Literal["append", "global_rerank"]

BEHAVIOR_FIELDS: frozenset[str] = frozenset(
    {
        "llm_model",
        "router_enabled",
        "router_model",
        "dense_top_k",
        "sparse_top_k",
        "sparse_overfetch_k",
        "rerank_top_n",
        "rerank_score_margin",
        "max_distance",
        "edge_expansion_enabled",
        "edge_hop_top_k",
        "parent_expansion_enabled",
        "parent_expansion_min_children",
        "parent_expansion_max_chars",
        "sibling_expansion_enabled",
        "sibling_expansion_radius",
        "sibling_expansion_max_chars",
        "sibling_expansion_max_tokens",
        "prefer_operative_enabled",
        "retrieval_operative_only",
        "consolidated_dedup_enabled",
        "min_chunks_for_answer",
        "answerability_gate_enabled",
        "evidence_gate",
        "evidence_judge_model",
        "corrective_retrieval_enabled",
        "faithfulness_selfcheck_enabled",
        "later_enacted_preference_enabled",
        "query_decomposition_enabled",
        "query_planner_model",
        "query_planner_max_subqueries",
        "subquery_packaging_enabled",
        "subquery_reserve_n",
        "corrective_mode",
        "corrective_max_facets",
        "corrective_facet_reserve_n",
        "adaptive_context_enabled",
        "adaptive_context_contract_version",
        "adaptive_context_floor",
        "adaptive_context_base_cap",
        "adaptive_context_uncertain_cap",
        "adaptive_context_multifacet_cap",
        "adaptive_context_stabilization_patience",
        "adaptive_context_token_target",
        "adaptive_context_token_estimator",
        "enable_query_rewriting",
    }
)

INFRA_FIELDS: frozenset[str] = frozenset(
    {
        "source_config_path",
        "db_path",
        "raw_data_dir",
        "normalized_data_dir",
        "request_timeout",
        "qdrant_collection",
        "qdrant_url",
        "qdrant_api_key",
        "bm25_path",
        "chunk_size",
        "chunk_overlap",
        "embedding_backend",
        "embedding_model",
        "embedding_dim",
        "embedding_query_instruction",
        "ollama_base_url",
        "reranker_backend",
        "reranker_model",
        "qwen3_reranker_model",
        "bedrock_rerank_model",
        "bedrock_rerank_region",
        "provision_supersession_path",
        "provision_status_path",
        "debug",
        "log_dir",
        "log_level",
        "log_to_file",
        "log_max_bytes",
        "log_backup_count",
        "trace_logging_enabled",
        "trace_max_text_preview",
        "eval_dataset_path",
        "eval_retrieval_targets_path",
        "eval_results_dir",
        "legal_query_rewrite_model",
        "legal_query_rewrite_timeout_seconds",
        "legal_query_rewrite_cache_dir",
        "eval_run_label",
        "ragas_score_cache_path",
        "ragas_llm_model",
        "ragas_embedding_model",
        "ragas_judge_backend",
        "ragas_openai_model",
        "anthropic_api_key",
        "openai_api_key",
        "max_conversation_turns",
        "answerability_gate_model",
        "crag_judge_model",
        "aws_region",
    }
)


@dataclass(frozen=True)
class AnswerPolicy:
    name: str
    generator_model: str
    strong_model: str | None
    router_enabled: bool
    router_model: str
    escalate_intents: frozenset[str]
    escalate_on_partial_evidence: bool
    retrieval_defaults: RetrievalKnobs
    evidence_gate: EvidenceGate
    evidence_judge_model: str
    min_chunks_for_answer: int
    corrective_retrieval_enabled: bool
    selfcheck_enabled: bool
    later_enacted_preference_enabled: bool
    query_decomposition_enabled: bool
    query_rewriting_enabled: bool
    corrective_mode: CorrectiveMode = "append"
    corrective_max_facets: int | None = None
    corrective_facet_reserve_n: int | None = None

    def __post_init__(self) -> None:
        if self.corrective_mode == "global_rerank" and not self.retrieval_defaults.adaptive_context_enabled:
            raise ValueError(
                "config error: corrective_mode='global_rerank' requires "
                "adaptive_context_enabled=True on retrieval_defaults — without adaptive "
                "packaging, global_rerank would dump the whole union into context "
                "unbounded (Phase 5 anti-pattern 5)."
            )
        if self.corrective_mode == "global_rerank" and self.retrieval_defaults.subquery_packaging_enabled:
            raise ValueError(
                "config error: corrective_mode='global_rerank' requires "
                "subquery_packaging_enabled=False on retrieval_defaults — under packaging, "
                "SelectionResult.retrieved holds packaged_retrieve's per-subquery output, "
                "not the pre-rerank fused pool that global_rerank's union step requires "
                "(Phase 5 design decision 2)."
            )

    @classmethod
    def from_settings(cls, settings_obj=settings, name: str = "local") -> "AnswerPolicy":
        return cls(
            name=name,
            generator_model=settings_obj.llm_model,
            strong_model=None,
            router_enabled=settings_obj.router_enabled,
            router_model=settings_obj.router_model,
            escalate_intents=frozenset(),
            escalate_on_partial_evidence=False,
            retrieval_defaults=RetrievalKnobs.from_settings(settings_obj),
            evidence_gate=(
                "answerability" if settings_obj.answerability_gate_enabled else "min_chunks"
            ),
            evidence_judge_model=settings_obj.answerability_gate_model,
            min_chunks_for_answer=settings_obj.min_chunks_for_answer,
            corrective_retrieval_enabled=False,
            selfcheck_enabled=settings_obj.faithfulness_selfcheck_enabled,
            later_enacted_preference_enabled=settings_obj.later_enacted_preference_enabled,
            query_decomposition_enabled=settings_obj.query_decomposition_enabled,
            query_rewriting_enabled=settings_obj.enable_query_rewriting,
            corrective_mode=settings_obj.corrective_mode,
            corrective_max_facets=settings_obj.corrective_max_facets,
            corrective_facet_reserve_n=settings_obj.corrective_facet_reserve_n,
        )

    def as_trace_dict(self) -> dict:
        data = asdict(self)
        data["escalate_intents"] = sorted(self.escalate_intents)
        data["retrieval_defaults"] = asdict(self.retrieval_defaults)
        return data


@dataclass(frozen=True)
class PolicyResolution:
    policy: AnswerPolicy
    policy_overrides: dict[str, object]
    env_ignored: dict[str, object]

    def summary(self) -> dict:
        return {
            "profile": self.policy.name,
            "policy_overrides": self.policy_overrides,
            "env_ignored": self.env_ignored,
        }


def _base_profile(name: str, settings_obj=settings) -> AnswerPolicy:
    if name == "corrective-global-rerank-experimental":
        # Phase 5 CP3 compares this eval-only arm against a control captured
        # through the settings-derived local policy. Inherit that same pass-1
        # policy so the declared evidence/corrective knobs are the only deltas;
        # the comparator and CP1 cache both fail closed on any drift.
        return replace(
            AnswerPolicy.from_settings(settings_obj, name=name),
            evidence_gate="crag",
            evidence_judge_model="claude-haiku-4-5",
            corrective_retrieval_enabled=True,
            corrective_mode="global_rerank",
            corrective_max_facets=3,
            corrective_facet_reserve_n=5,
        )

    base = AnswerPolicy(
        name=name,
        generator_model="gemma4:e4b",
        strong_model=None,
        router_enabled=False,
        router_model="claude-haiku-4-5",
        escalate_intents=frozenset(),
        escalate_on_partial_evidence=False,
        retrieval_defaults=RetrievalKnobs(
            dense_top_k=30,
            sparse_top_k=10,
            sparse_overfetch_k=100,
            rerank_top_n=8,
            rerank_score_margin=6.0,
            max_distance=0.5,
            edge_expansion_enabled=True,
            edge_hop_top_k=3,
            parent_expansion_enabled=True,
            parent_expansion_min_children=2,
            parent_expansion_max_chars=8000,
            query_planner_model="mistral",
            query_planner_max_subqueries=3,
            prefer_operative_enabled=False,
            retrieval_operative_only=True,
            consolidated_dedup_enabled=True,
            subquery_packaging_enabled=False,
            subquery_reserve_n=2,
        ),
        evidence_gate="min_chunks",
        evidence_judge_model=settings_obj.answerability_gate_model,
        min_chunks_for_answer=1,
        corrective_retrieval_enabled=False,
        selfcheck_enabled=False,
        later_enacted_preference_enabled=False,
        query_decomposition_enabled=False,
        query_rewriting_enabled=True,
    )
    if name == "cloud":
        return replace(base, router_enabled=True)
    if name == "eval":
        return base
    if name == "cascade":
        return replace(
            base,
            router_enabled=True,
            strong_model="claude-haiku-4-5",
            escalate_intents=frozenset(
                {"list_or_rule_synthesis", "amendment_or_current_law"}
            ),
        )
    if name == "local-cascade":
        return replace(
            base,
            router_enabled=True,
            router_model="gemma3:4b",
            strong_model="gemma3:4b",
            escalate_intents=frozenset(
                {"list_or_rule_synthesis", "amendment_or_current_law"}
            ),
        )
    if name == "crag-experimental":
        # Intentionally remains on the pinned legacy base for CP1-era
        # reproducibility; only the Phase 5 global-rerank arm tracks control.
        return replace(
            base,
            generator_model=settings_obj.llm_model,
            router_enabled=settings_obj.router_enabled,
            router_model=settings_obj.router_model,
            evidence_gate="crag",
            evidence_judge_model=settings_obj.crag_judge_model,
            corrective_retrieval_enabled=True,
        )
    raise ValueError(f"Unknown RAGLAB_PROFILE: {name}")


def _policy_value(policy: AnswerPolicy, field: str) -> object:
    if field == "llm_model":
        return policy.generator_model
    if field == "answerability_gate_enabled":
        return policy.evidence_gate == "answerability"
    if field == "evidence_judge_model":
        return policy.evidence_judge_model
    if field == "faithfulness_selfcheck_enabled":
        return policy.selfcheck_enabled
    if field == "enable_query_rewriting":
        return policy.query_rewriting_enabled
    if hasattr(policy, field):
        return getattr(policy, field)
    if hasattr(policy.retrieval_defaults, field):
        return getattr(policy.retrieval_defaults, field)
    raise KeyError(field)


def resolve_policy(settings_obj=settings) -> PolicyResolution:
    profile_name = settings_obj.raglab_profile
    if profile_name == "local":
        return PolicyResolution(
            policy=AnswerPolicy.from_settings(settings_obj),
            policy_overrides={},
            env_ignored={},
        )

    policy = _base_profile(profile_name, settings_obj=settings_obj)
    overrides: dict[str, object] = {}
    ignored: dict[str, object] = {}
    local_policy = AnswerPolicy.from_settings(settings_obj, name=profile_name)

    for field in sorted(BEHAVIOR_FIELDS):
        policy_value = _policy_value(policy, field)
        settings_value = _policy_value(local_policy, field)
        if policy_value != settings_value:
            overrides[field] = policy_value
            ignored[field] = settings_value

    return PolicyResolution(policy=policy, policy_overrides=overrides, env_ignored=ignored)
