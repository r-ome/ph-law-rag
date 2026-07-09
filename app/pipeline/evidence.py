from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, EvidenceReport
from app.retriever.answerability import is_answerable


def evaluate_evidence(state: AnswerState, policy: AnswerPolicy) -> EvidenceReport:
    question = state.effective_question or state.question
    pre_expansion_count = len(state.selection.pre_expansion)
    selected_count = len(state.selection.selected)

    if policy.evidence_gate == "crag":
        raise NotImplementedError(
            "RAGLAB_PROFILE=crag-experimental is registered, but the CRAG evidence gate "
            "is not implemented until PR5."
        )

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

    return EvidenceReport(
        verdict="sufficient",
        method="min_chunks",
        missing_facets=[],
        detail=base_detail,
    )
