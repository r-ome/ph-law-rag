from dataclasses import replace

import pytest

from app.config import Settings, settings
from app.pipeline.policy import AnswerPolicy, BEHAVIOR_FIELDS, INFRA_FIELDS, resolve_policy
from app.retriever.strategy import RetrievalKnobs

pytestmark = pytest.mark.unit


def test_local_profile_mirrors_current_settings(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "local")
    monkeypatch.setattr(settings, "llm_model", "qwen3:4b")
    monkeypatch.setattr(settings, "router_enabled", True)
    monkeypatch.setattr(settings, "dense_top_k", 41)
    monkeypatch.setattr(settings, "min_chunks_for_answer", 3)
    monkeypatch.setattr(settings, "faithfulness_selfcheck_enabled", True)

    resolution = resolve_policy()

    assert resolution.policy == AnswerPolicy.from_settings(settings)
    assert resolution.policy.generator_model == "qwen3:4b"
    assert resolution.policy.router_enabled is True
    assert resolution.policy.retrieval_defaults.dense_top_k == 41
    assert resolution.policy.min_chunks_for_answer == 3
    assert resolution.policy.selfcheck_enabled is True
    assert resolution.policy_overrides == {}
    assert resolution.env_ignored == {}


def test_named_profile_overrides_behavior_settings(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "cloud")
    monkeypatch.setattr(settings, "llm_model", "claude-haiku-4-5")
    monkeypatch.setattr(settings, "router_enabled", False)
    monkeypatch.setattr(settings, "dense_top_k", 99)
    monkeypatch.setattr(settings, "min_chunks_for_answer", 7)

    resolution = resolve_policy()

    assert resolution.policy.name == "cloud"
    assert resolution.policy.generator_model == "gemma4:e4b"
    assert resolution.policy.router_enabled is True
    assert resolution.policy.retrieval_defaults.dense_top_k == 30
    assert resolution.policy.min_chunks_for_answer == 1
    assert resolution.policy_overrides["llm_model"] == "gemma4:e4b"
    assert resolution.env_ignored["llm_model"] == "claude-haiku-4-5"
    assert resolution.policy_overrides["router_enabled"] is True
    assert resolution.env_ignored["router_enabled"] is False
    assert resolution.policy_overrides["dense_top_k"] == 30
    assert resolution.env_ignored["dense_top_k"] == 99


def test_infra_settings_are_not_policy_overridden(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "cloud")
    monkeypatch.setattr(settings, "qdrant_url", "https://qdrant.example")
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.example")
    monkeypatch.setattr(settings, "embedding_backend", "bedrock")

    resolution = resolve_policy()

    assert "qdrant_url" in INFRA_FIELDS
    assert "ollama_base_url" in INFRA_FIELDS
    assert "embedding_backend" in INFRA_FIELDS
    assert "qdrant_url" not in resolution.policy_overrides
    assert "ollama_base_url" not in resolution.policy_overrides
    assert "embedding_backend" not in resolution.policy_overrides


def test_settings_field_ownership_is_exhaustive():
    assert set(Settings.model_fields) - BEHAVIOR_FIELDS - INFRA_FIELDS == {"raglab_profile"}


def test_sibling_knobs_are_behavior_fields():
    assert {
        "sibling_expansion_enabled",
        "sibling_expansion_radius",
        "sibling_expansion_max_chars",
        "sibling_expansion_max_tokens",
    } <= BEHAVIOR_FIELDS


def test_adaptive_context_default_on_with_single_flag_rollback(tmp_path, monkeypatch):
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.delenv("ADAPTIVE_CONTEXT_ENABLED", raising=False)

    default_settings = Settings(_env_file=empty_env)
    assert default_settings.adaptive_context_enabled is True
    assert AnswerPolicy.from_settings(
        default_settings
    ).retrieval_defaults.adaptive_context_enabled is True

    rollback_env = tmp_path / "rollback.env"
    rollback_env.write_text("adaptive_context_enabled=false\n", encoding="utf-8")
    rollback_settings = Settings(_env_file=rollback_env)
    assert rollback_settings.adaptive_context_enabled is False
    assert AnswerPolicy.from_settings(
        rollback_settings
    ).retrieval_defaults.adaptive_context_enabled is False


def test_eval_profile_does_not_turn_on_router(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "eval")
    monkeypatch.setattr(settings, "router_enabled", True)

    resolution = resolve_policy()

    assert resolution.policy.name == "eval"
    assert resolution.policy.router_enabled is False
    assert resolution.policy_overrides["router_enabled"] is False
    assert resolution.env_ignored["router_enabled"] is True


def test_cascade_profiles_define_expected_escalation(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "cascade")
    cascade = resolve_policy().policy

    assert cascade.router_enabled is True
    assert cascade.router_model == "claude-haiku-4-5"
    assert cascade.strong_model == "claude-haiku-4-5"
    assert cascade.escalate_intents == frozenset(
        {"list_or_rule_synthesis", "amendment_or_current_law"}
    )

    monkeypatch.setattr(settings, "raglab_profile", "local-cascade")
    local_cascade = resolve_policy().policy

    assert local_cascade.router_enabled is True
    assert local_cascade.router_model == "gemma3:4b"
    assert local_cascade.strong_model == "gemma3:4b"
    assert local_cascade.escalate_intents == cascade.escalate_intents


def test_policy_trace_dict_serializes_frozenset_and_knobs():
    policy = replace(
        AnswerPolicy.from_settings(settings),
        escalate_intents=frozenset({"b", "a"}),
        retrieval_defaults=RetrievalKnobs(
            dense_top_k=1,
            sparse_top_k=2,
            rerank_top_n=3,
            parent_expansion_enabled=True,
            prefer_operative_enabled=False,
            retrieval_operative_only=True,
            consolidated_dedup_enabled=True,
        ),
    )

    assert policy.as_trace_dict()["escalate_intents"] == ["a", "b"]
    assert policy.as_trace_dict()["retrieval_defaults"]["dense_top_k"] == 1


def test_crag_profile_is_registered_with_pinned_judge_and_corrective(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "crag-experimental")
    monkeypatch.setattr(settings, "answerability_gate_model", "mistral")

    resolution = resolve_policy()

    assert resolution.policy.name == "crag-experimental"
    assert resolution.policy.evidence_gate == "crag"
    assert resolution.policy.evidence_judge_model == "claude-haiku-4-5"
    assert resolution.policy.corrective_retrieval_enabled is True
    assert resolution.policy_overrides["evidence_judge_model"] == "claude-haiku-4-5"
    assert resolution.env_ignored["evidence_judge_model"] == "mistral"
    # Phase 5 CP2: crag-experimental stays pinned to the legacy append mode so
    # CP1's sealed audit artifacts remain reproducible under their original config.
    assert resolution.policy.corrective_mode == "append"
    assert resolution.policy.corrective_max_facets is None
    assert resolution.policy.corrective_facet_reserve_n is None


def test_corrective_global_rerank_profile_is_registered(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "corrective-global-rerank-experimental")

    resolution = resolve_policy()

    assert resolution.policy.name == "corrective-global-rerank-experimental"
    assert resolution.policy.evidence_gate == "crag"
    assert resolution.policy.evidence_judge_model == "claude-haiku-4-5"
    assert resolution.policy.corrective_retrieval_enabled is True
    assert resolution.policy.corrective_mode == "global_rerank"
    assert resolution.policy.corrective_max_facets == 3
    assert resolution.policy.corrective_facet_reserve_n == 5
    # adaptive context stays enabled — required by the __post_init__ guard.
    assert resolution.policy.retrieval_defaults.adaptive_context_enabled is True


def test_corrective_global_rerank_knobs_are_behavior_fields():
    assert {
        "corrective_mode",
        "corrective_max_facets",
        "corrective_facet_reserve_n",
    } <= BEHAVIOR_FIELDS


@pytest.mark.parametrize(
    "profile_name", ["local", "cloud", "eval", "cascade", "local-cascade"]
)
def test_shipping_profiles_are_unaffected_by_phase5_corrective_knobs(monkeypatch, profile_name):
    """Phase 5 CP2 adds corrective_mode/corrective_max_facets/corrective_facet_reserve_n;
    every shipping profile must default to the legacy append/None/None triple —
    provably unaffected by the new global_rerank mechanism."""
    monkeypatch.setattr(settings, "raglab_profile", profile_name)

    policy = resolve_policy().policy

    assert policy.corrective_mode == "append"
    assert policy.corrective_max_facets is None
    assert policy.corrective_facet_reserve_n is None
