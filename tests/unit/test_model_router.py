from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.pipeline.model_router import select_model
from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, EvidenceReport

pytestmark = pytest.mark.unit


def _policy(**overrides):
    policy = AnswerPolicy.from_settings()
    return replace(policy, **overrides)


def test_select_model_uses_generator_when_no_strong_model():
    policy = _policy(generator_model="mistral", strong_model=None)
    state = AnswerState(question="What is theft?", debug_enabled=False)

    choice = select_model(policy, state)

    assert choice.model == "mistral"
    assert choice.reason == "policy_default"


def test_select_model_escalates_matching_intent():
    policy = _policy(
        generator_model="mistral",
        strong_model="claude-haiku-4-5",
        escalate_intents=frozenset({"amendment_or_current_law"}),
    )
    state = AnswerState(question="Which law controls now?", debug_enabled=False)
    state.router_decision = SimpleNamespace(routed_intent="amendment_or_current_law")

    choice = select_model(policy, state)

    assert choice.model == "claude-haiku-4-5"
    assert choice.reason == "intent:amendment_or_current_law"


def test_select_model_does_not_escalate_non_matching_intent():
    policy = _policy(
        generator_model="mistral",
        strong_model="claude-haiku-4-5",
        escalate_intents=frozenset({"amendment_or_current_law"}),
    )
    state = AnswerState(question="What is Article 315?", debug_enabled=False)
    state.router_decision = SimpleNamespace(routed_intent="citation_lookup")

    choice = select_model(policy, state)

    assert choice.model == "mistral"
    assert choice.reason == "policy_default"


def test_select_model_escalates_partial_evidence_when_policy_allows():
    policy = _policy(
        generator_model="mistral",
        strong_model="claude-haiku-4-5",
        escalate_on_partial_evidence=True,
    )
    state = AnswerState(question="What penalties apply?", debug_enabled=False)
    state.evidence = EvidenceReport(
        verdict="partial",
        method="crag_facets",
        missing_facets=["penalty clause"],
        detail={},
    )

    choice = select_model(policy, state)

    assert choice.model == "claude-haiku-4-5"
    assert choice.reason == "evidence:partial"
