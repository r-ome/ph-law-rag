from app.observability.logger import get_logger
from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, EvidenceReport
from app.retriever.answerability import _gate_complete, is_answerable
from app.retriever.facet_checker import (
    _CRAG_SYSTEM,
    _parse_crag_output,
    _render_crag_prompt,
)
from app.retriever.types import RetrievalResult

logger = get_logger(__name__)


def _check_crag_facets(
    question: str,
    chunks: list[RetrievalResult],
    *,
    model: str,
) -> tuple[str, list[str], dict]:
    if not chunks:
        return "sufficient", [], {"facets": [], "present": [], "missing": []}

    user_prompt = _render_crag_prompt(question, chunks)
    output: str | None = None
    error: str | None = None
    try:
        output = _gate_complete(_CRAG_SYSTEM, user_prompt, model, max_tokens=512)
        parsed = _parse_crag_output(output)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        parsed = None

    if parsed is None:
        logger.warning(
            "crag_judge_failed",
            model=model,
            error=error,
            judge_output=output,
        )
        return "sufficient", [], {
            "facets": [],
            "present": [],
            "missing": [],
            "judge_output": output,
            "judge_error": error,
            "parse_failed": True,
        }

    facets, present, missing, verdict = parsed
    logger.info(
        "crag_judge_verdict",
        model=model,
        verdict=verdict,
        facet_count=len(facets),
        missing_count=len(missing),
        judge_output=output,
    )
    return verdict, missing, {
        "facets": facets,
        "present": present,
        "missing": missing,
        "judge_output": output,
    }


def _check_crag_facets_cached(
    question: str,
    chunks: list[RetrievalResult],
    *,
    model: str,
    row_label: str,
    authorize_paid_calls: bool,
) -> tuple[str, list[str], dict]:
    """Facet-checker call routed through the Phase 5 CP1 cache
    (app.retriever.facet_checker — the same cache app.evals.facet_audit built).

    Used only by the corrective_mode='global_rerank' eval arm. Cache hit -> zero
    network. Cache miss -> fail closed (RuntimeError naming the row) unless
    authorize_paid_calls is explicitly set — no paid call ever happens implicitly.
    """
    if not chunks:
        return "sufficient", [], {"facets": [], "present": [], "missing": []}

    from app.retriever.facet_checker import call_and_cache, cached_decision

    rendered_prompt = _render_crag_prompt(question, chunks)
    decision = cached_decision(rendered_prompt, model=model)
    if decision is None:
        if not authorize_paid_calls:
            raise RuntimeError(
                f"facet-checker cache miss for row {row_label!r}: no cached CP1 "
                "decision for this (question, selected-context, model) and paid "
                "calls are not authorized. Set authorize_paid_calls=True to allow "
                "a live Haiku call (which will then be cached)."
            )
        decision = call_and_cache(rendered_prompt, model=model)

    if decision.operational_fallback:
        logger.warning(
            "crag_judge_cache_fallback",
            model=model,
            row_label=row_label,
            error=decision.judge_error,
        )
        return "sufficient", [], {
            "facets": [],
            "present": [],
            "missing": [],
            "judge_error": decision.judge_error,
            "cache_status": decision.cache_status,
            "parse_failed": True,
        }

    return decision.verdict, decision.missing, {
        "facets": decision.facets,
        "present": decision.present,
        "missing": decision.missing,
        "cache_status": decision.cache_status,
    }


def evaluate_evidence(
    state: AnswerState, policy: AnswerPolicy, *, authorize_paid_calls: bool = False
) -> EvidenceReport:
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
        global_rerank_mode = policy.corrective_mode == "global_rerank"
        # Phase 5 decision 1: global_rerank's checker input is the pass-1
        # adaptive-selected context (what generation would see), not
        # pre_expansion. The legacy append mode keeps PR5's pre_expansion input
        # unchanged.
        checker_context = (
            state.selection.selected if global_rerank_mode else state.selection.pre_expansion
        )
        if global_rerank_mode:
            verdict, missing_facets, detail = _check_crag_facets_cached(
                question,
                checker_context,
                model=policy.evidence_judge_model,
                row_label=state.eval_id or state.session_id or question,
                authorize_paid_calls=authorize_paid_calls,
            )
        else:
            verdict, missing_facets, detail = _check_crag_facets(
                question,
                checker_context,
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
