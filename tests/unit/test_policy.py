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
    assert resolution.policy.generator_model == "mistral"
    assert resolution.policy.router_enabled is True
    assert resolution.policy.retrieval_defaults.dense_top_k == 30
    assert resolution.policy.min_chunks_for_answer == 1
    assert resolution.policy_overrides["llm_model"] == "mistral"
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


def test_eval_profile_does_not_turn_on_router(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "eval")
    monkeypatch.setattr(settings, "router_enabled", True)

    resolution = resolve_policy()

    assert resolution.policy.name == "eval"
    assert resolution.policy.router_enabled is False
    assert resolution.policy_overrides["router_enabled"] is False
    assert resolution.env_ignored["router_enabled"] is True


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


def test_crag_profile_is_registered_but_not_implemented(monkeypatch):
    monkeypatch.setattr(settings, "raglab_profile", "crag-experimental")

    with pytest.raises(NotImplementedError, match="CRAG evidence gate"):
        resolve_policy()
