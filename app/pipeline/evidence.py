from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, EvidenceReport
from app.retriever.answerability import _gate_complete, is_answerable
from app.retriever.context_builder import build_context
from app.retriever.types import RetrievalResult

_CRAG_SYSTEM = """You are a facet checker for a Philippine-law retrieval system.
A "facet" is a SUBSTANTIVE legal element the question needs answered — a rule,
element, penalty, requirement, or exception. It is NOT a matter of wording.

List the facets the question needs, then check whether the passages supply the
substance of each (the rule/number/element itself), even if worded differently.

A facet is MISSING only if the passages do not contain the substantive law needed
to answer it. Do NOT flag a facet as missing for any of these reasons:
- the passages don't cite a specific article/section number
- the passages don't state the rule in one consolidated sentence
- you would prefer a fuller, more exhaustive, or more definitive phrasing
- the answer must be inferred by combining two passages
If every needed rule/element is present in substance, return sufficient.
When uncertain, prefer sufficient. Never return insufficient.

Reply in exactly this format:
FACETS: <semicolon-separated substantive facets the question needs>
PRESENT: <semicolon-separated facets whose substance the passages supply>
MISSING: <semicolon-separated facets whose substance is absent; write "none" if all present>
VERDICT: sufficient | partial"""


# Judges routinely fill MISSING with a "no gaps" sentinel instead of leaving it
# blank; treat those as empty so they don't count as a missing facet (→ partial).
_NULL_FACETS = {"none", "nothing", "n/a", "na", "n.a.", "-", "no missing facets"}


def _split_facets(value: str) -> list[str]:
    return [
        part.strip()
        for part in value.split(";")
        if part.strip() and part.strip().lower() not in _NULL_FACETS
    ]


def _parse_crag_output(output: str) -> tuple[list[str], list[str], list[str], str] | None:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        if key in {"FACETS", "PRESENT", "MISSING", "VERDICT"}:
            fields[key] = value.strip()

    verdict = fields.get("VERDICT", "").lower()
    if verdict not in {"sufficient", "partial"}:
        return None

    facets = _split_facets(fields.get("FACETS", ""))
    present = _split_facets(fields.get("PRESENT", ""))
    missing = _split_facets(fields.get("MISSING", ""))
    final_verdict = "partial" if missing else "sufficient"
    return facets, present, missing, final_verdict


def _check_crag_facets(
    question: str,
    chunks: list[RetrievalResult],
    *,
    model: str,
) -> tuple[str, list[str], dict]:
    if not chunks:
        return "sufficient", [], {"facets": [], "present": [], "missing": []}

    context_block, _ = build_context(chunks)
    user_prompt = f"""Passages:
{context_block}

Question: {question}

FACETS:"""
    try:
        output = _gate_complete(_CRAG_SYSTEM, user_prompt, model, max_tokens=512)
        parsed = _parse_crag_output(output)
    except Exception:
        parsed = None

    if parsed is None:
        return "sufficient", [], {"facets": [], "present": [], "missing": []}

    facets, present, missing, verdict = parsed
    return verdict, missing, {"facets": facets, "present": present, "missing": missing}


def evaluate_evidence(state: AnswerState, policy: AnswerPolicy) -> EvidenceReport:
    question = state.effective_question or state.question
    pre_expansion_count = len(state.selection.pre_expansion)
    selected_count = len(state.selection.selected)

    base_detail = {
        "pre_expansion_count": pre_expansion_count,
        "selected_count": selected_count,
        "min_chunks_for_answer": policy.min_chunks_for_answer,
    }
    if pre_expansion_count < policy.min_chunks_for_answer:
        return EvidenceReport(
            verdict="insufficient",
            method="min_chunks",
            missing_facets=[],
            detail=base_detail,
        )

    if policy.evidence_gate == "answerability":
        answerable = is_answerable(question, state.selection.pre_expansion)
        return EvidenceReport(
            verdict="sufficient" if answerable else "insufficient",
            method="answerability_gate",
            missing_facets=[],
            detail={**base_detail, "answerable": answerable},
        )

    if policy.evidence_gate == "crag":
        verdict, missing_facets, detail = _check_crag_facets(
            question,
            state.selection.pre_expansion,
            model=policy.evidence_judge_model,
        )
        return EvidenceReport(
            verdict=verdict,
            method="crag_facets",
            missing_facets=missing_facets,
            detail={**base_detail, **detail},
        )

    return EvidenceReport(
        verdict="sufficient",
        method="min_chunks",
        missing_facets=[],
        detail=base_detail,
    )
