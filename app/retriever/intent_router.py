"""Intent router (R4): Haiku classifier in front of retrieval.

The classifier emits {"intent", "confidence"}; low confidence, parse failure,
or any LLM/backend error routes to "default". Intent and strategy are distinct
namespaces: INTENT_TO_STRATEGY is many-to-one, and trace rows like
intent=out_of_scope / strategy=default are how label-only lanes earn promotion.

Prompt + few-shots are the R1 v1 text verbatim (benchmarked 2026-07-07, run
intent_ab_20260707_210542). scripts/classify_intent_ab.py imports them from
here as the single source. Any edit is prompt v2: update the prompt hash-pin
test deliberately and re-benchmark before trusting routed accuracy. router_model
uses the benchmarked alias "claude-haiku-4-5", not a dated snapshot; that is an
accepted drift risk.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from app.config import settings
from app.observability.context import record_stage
from app.observability.logger import get_logger

logger = get_logger(__name__)

INTENTS = [
    "default",
    "citation_lookup",
    "list_or_rule_synthesis",
    "amendment_or_current_law",
    "out_of_scope",
]
VALID_INTENTS = frozenset(INTENTS)

# Many-to-one: label-only intents map to "default" (rule 4). current_law is the
# only registered non-default preset (R3, eval_051 evidence, MiniLM serving).
INTENT_TO_STRATEGY = {
    "default": "default",
    "citation_lookup": "default",
    "list_or_rule_synthesis": "default",
    "amendment_or_current_law": "current_law",
    "out_of_scope": "default",
}

FEW_SHOTS = [
    ("Can police make a warrantless arrest right after seeing a theft?", "default"),
    ("Is a notarized contract automatically enforceable in court?", "default"),
    ("What does Article 315 of the Revised Penal Code say?", "citation_lookup"),
    ("What does Section 5 of RA 9165 provide?", "citation_lookup"),
    ("List the essential elements of donation under Philippine civil law.", "list_or_rule_synthesis"),
    ("What are the just causes for terminating employment?", "list_or_rule_synthesis"),
    ("Which rule controls now after a later statute changed an old penalty amount?", "amendment_or_current_law"),
    ("Was the age threshold for sexual consent changed by a later law?", "amendment_or_current_law"),
    ("How do I file my income tax return this year?", "out_of_scope"),
    ("What documents do I need for a Canadian tourist visa?", "out_of_scope"),
]

SYSTEM_PROMPT = """You classify Philippine-law questions for a retrieval router.

Return exactly one JSON object and nothing else:
{{"intent": "...", "confidence": "high"|"low"}}

Valid intents:
- default: ordinary legal information, paraphrase, case-law, or cross-source questions that do not require a special router lane.
- citation_lookup: asks what a specific article, section, statute, rule, or legal citation says. This is about locating cited text, not deciding whether the cited law is still current.
- list_or_rule_synthesis: asks for a list, elements, requisites, grounds, rights, duties, factors, or a rule set.
- amendment_or_current_law: asks whether law has changed, what current law controls, how an old rule was amended, repealed, superseded, or replaced, or how a later law affects an older one.
- out_of_scope: asks about a topic outside the indexed Philippine law corpus, such as tax computation, visas, local permit details, social benefits computation, customs rates, securities registration, or procedural deadlines not covered by the corpus.

Tie-breaking:
- If a question asks whether an old cited provision is still current, choose amendment_or_current_law, not citation_lookup.
- If an out-of-scope topic is legal but the corpus does not cover it, choose out_of_scope.
- Use confidence "low" only when the router should fall back to default retrieval.

Few-shot examples:
{few_shots}
"""


def rendered_few_shots() -> str:
    lines = []
    for question, intent in FEW_SHOTS:
        lines.append(f"Q: {question}")
        lines.append(f'A: {{"intent": "{intent}", "confidence": "high"}}')
    return "\n".join(lines)


def render_llm_prompts(question: str) -> tuple[str, str]:
    system = SYSTEM_PROMPT.format(few_shots=rendered_few_shots())
    user = f"Question: {question}\nReturn the JSON object only."
    return system, user


def strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```") or not text.endswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 2:
        return text
    first = lines[0].strip()
    last = lines[-1].strip()
    if first.startswith("```") and last == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def parse_prediction(raw: str) -> tuple[str, str] | None:
    try:
        data = json.loads(strip_code_fence(raw))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or set(data) != {"intent", "confidence"}:
        return None
    intent = data.get("intent")
    confidence = data.get("confidence")
    if intent not in VALID_INTENTS or confidence not in {"high", "low"}:
        return None
    return intent, confidence


@dataclass(frozen=True)
class RouterDecision:
    intent: str | None
    confidence: str | None
    routed_intent: str
    strategy: str
    parse_ok: bool
    fallback_reason: str | None
    error: str | None
    latency_ms: float

    def as_trace_dict(self) -> dict:
        return asdict(self)


DEFAULT_DECISION_FIELDS = dict(
    intent=None,
    confidence=None,
    routed_intent="default",
    strategy="default",
    parse_ok=False,
)


def classify_with_raw(question: str) -> tuple[RouterDecision, str | None]:
    """Classify a standalone question and return the raw LLM text for eval audits."""
    from app.retriever.llm_client import generate

    system, user = render_llm_prompts(question)
    start = time.perf_counter()
    error: str | None = None
    raw: str | None = None
    try:
        raw = generate(system, user, model=settings.router_model)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - start) * 1000

    if error is not None:
        decision = RouterDecision(
            **DEFAULT_DECISION_FIELDS,
            fallback_reason="llm_error",
            error=error,
            latency_ms=elapsed_ms,
        )
    else:
        parsed = parse_prediction(raw or "")
        if parsed is None:
            decision = RouterDecision(
                **DEFAULT_DECISION_FIELDS,
                fallback_reason="parse_error",
                error="unparseable classifier output",
                latency_ms=elapsed_ms,
            )
        else:
            intent, confidence = parsed
            routed = intent if confidence == "high" else "default"
            decision = RouterDecision(
                intent=intent,
                confidence=confidence,
                routed_intent=routed,
                strategy=INTENT_TO_STRATEGY[routed],
                parse_ok=True,
                fallback_reason=None if confidence == "high" else "low_confidence",
                error=None,
                latency_ms=elapsed_ms,
            )

    record_stage(
        "intent_router",
        ms=elapsed_ms,
        model=settings.router_model,
        intent=decision.intent,
        confidence=decision.confidence,
        routed_intent=decision.routed_intent,
        strategy=decision.strategy,
        fallback_reason=decision.fallback_reason,
        error=decision.error,
    )
    if decision.error:
        logger.warning(
            "intent_router_fallback",
            error=decision.error,
            fallback_reason=decision.fallback_reason,
        )
    return decision, raw


def classify(question: str) -> RouterDecision:
    """Classify a standalone question. Never raises; failures route to default."""
    decision, _raw = classify_with_raw(question)
    return decision
