from app.config import settings
from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState
from app.retriever.dedup import dedup_results
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.subquery_retrieval import round_robin_merge
from app.retriever.types import RetrievalResult


def _targeted_query(facet: str, question: str) -> str:
    return f"{facet} - {question}" if question and question not in facet else facet


def _dedup_additions(
    selected: list[RetrievalResult],
    candidates: list[RetrievalResult],
) -> list[RetrievalResult]:
    selected_ids = {result.chunk_id for result in selected}
    if not candidates:
        return []

    deduped = dedup_results([*selected, *candidates])
    kept_candidate_ids = {
        result.chunk_id for result in deduped if result.chunk_id not in selected_ids
    }
    return [result for result in candidates if result.chunk_id in kept_candidate_ids]


def _relevant_to_question(
    question: str,
    additions: list[RetrievalResult],
    knobs,
) -> list[RetrievalResult]:
    """Keep only additions that are relevant to the ORIGINAL question, not just to
    the facet phrase. Facet queries can surface tangential chunks (e.g. on
    out-of-scope questions); injecting them can tip an abstention into a
    confabulation. Re-score against the question and apply the rerank margin."""
    if not additions:
        return additions
    rescored = rerank(question, additions, knobs=knobs)
    margin = knobs.rerank_score_margin if knobs else settings.rerank_score_margin
    top = rescored[0].score
    return [result for result in rescored if result.score >= top - margin]


def corrective_retrieve(state: AnswerState, policy: AnswerPolicy) -> AnswerState:
    """Run one bounded curated-corpus retrieval round for missing CRAG facets."""
    baseline_count = len(state.selection.selected)
    max_added = max(0, policy.retrieval_defaults.subquery_reserve_n)
    state.corrective_baseline_selected_count = baseline_count
    state.corrective_post_selected_count = baseline_count
    state.corrective_max_added = max_added

    missing_facets = state.evidence.missing_facets if state.evidence else []
    if not missing_facets or max_added == 0:
        state.corrective_ran = False
        state.corrective_added_chunks = 0
        return state

    question = state.effective_question or state.question
    per_facet: list[list[RetrievalResult]] = []
    for facet in missing_facets:
        query = _targeted_query(facet, question)
        retrieved = hybrid_retriever(query, knobs=state.strategy_knobs)
        per_facet.append(rerank(query, retrieved, knobs=state.strategy_knobs)[:max_added])

    selected_ids = {result.chunk_id for result in state.selection.selected}
    merged = round_robin_merge(per_facet, seen_ids=selected_ids, cap=max_added)
    additions = _dedup_additions(state.selection.selected, merged)[:max_added]
    additions = _relevant_to_question(question, additions, state.strategy_knobs)

    state.selection.selected = [*state.selection.selected, *additions]
    state.corrective_ran = bool(additions)
    state.corrective_added_chunks = len(additions)
    state.corrective_post_selected_count = len(state.selection.selected)
    return state
