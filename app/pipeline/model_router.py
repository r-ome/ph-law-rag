from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, ModelChoice


def select_model(policy: AnswerPolicy, state: AnswerState) -> ModelChoice:
    if policy.strong_model is None:
        return ModelChoice(policy.generator_model, "policy_default")

    intent = None
    if state.router_decision is not None:
        intent = getattr(state.router_decision, "routed_intent", None)

    if intent in policy.escalate_intents:
        return ModelChoice(policy.strong_model, f"intent:{intent}")

    evidence = getattr(state, "evidence", None)
    if (
        policy.escalate_on_partial_evidence
        and evidence is not None
        and getattr(evidence, "verdict", None) == "partial"
    ):
        return ModelChoice(policy.strong_model, "evidence:partial")

    return ModelChoice(policy.generator_model, "policy_default")
