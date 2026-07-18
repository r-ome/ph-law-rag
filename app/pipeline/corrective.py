from app.config import settings
from app.observability.context import capture_candidates, stage_timer
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
    rescored = rerank(
        question,
        additions,
        knobs=knobs,
        query_variant="original",
        query_ordinal=0,
    )
    margin = knobs.rerank_score_margin if knobs else settings.rerank_score_margin
    top = rescored[0].score
    return [result for result in rescored if result.score >= top - margin]


def corrective_retrieve(state: AnswerState, policy: AnswerPolicy) -> AnswerState:
    """Dispatch to the active corrective mode (Phase 5 CP2).

    ``append`` (default) is the legacy PR5 additive-packaging mechanism,
    unchanged. ``global_rerank`` is the Phase 5 eval-only union+re-select
    mechanism (docs/retrieval_strategy_review.md, "Phase 5 plan").
    """
    if policy.corrective_mode == "global_rerank":
        return _corrective_retrieve_global_rerank(state, policy)
    return _corrective_retrieve_append(state, policy)


def _corrective_retrieve_append(state: AnswerState, policy: AnswerPolicy) -> AnswerState:
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
    for facet_index, facet in enumerate(missing_facets, start=1):
        query = _targeted_query(facet, question)
        retrieved = hybrid_retriever(
            query,
            knobs=state.strategy_knobs,
            query_variant="facet",
            query_ordinal=facet_index,
        )
        facet_results = rerank(
            query,
            retrieved,
            knobs=state.strategy_knobs,
            query_variant="facet",
            query_ordinal=facet_index,
        )[:max_added]
        capture_candidates(
            "corrective_facet",
            facet_results,
            query_variant="facet",
            query_text=query,
            query_ordinal=facet_index,
            selected_ids={result.chunk_id for result in facet_results},
        )
        per_facet.append(facet_results)

    selected_ids = {result.chunk_id for result in state.selection.selected}
    merged = round_robin_merge(per_facet, seen_ids=selected_ids, cap=max_added)
    additions = _dedup_additions(state.selection.selected, merged)[:max_added]
    additions = _relevant_to_question(question, additions, state.strategy_knobs)
    capture_candidates(
        "corrective_filtered",
        additions,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        selected_ids={result.chunk_id for result in additions},
    )

    state.selection.selected = [*state.selection.selected, *additions]
    capture_candidates(
        "corrective",
        state.selection.selected,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        selected_ids={result.chunk_id for result in state.selection.selected},
    )
    state.corrective_ran = bool(additions)
    state.corrective_added_chunks = len(additions)
    state.corrective_post_selected_count = len(state.selection.selected)
    return state


def _union_by_chunk_id(
    *groups: list[RetrievalResult],
) -> list[RetrievalResult]:
    """Union preserving first-seen order/instance; dedup is chunk_id only here.

    Phase 5 design decision 5: everything beyond chunk_id dedup (represented
    merged chunks, exact normalized text) is left to the reused dedup_results
    call inside the serving selection tail — no provision-family collapse is
    applied here, so distinct sibling leaves survive the union step.
    """
    seen: set[str] = set()
    union: list[RetrievalResult] = []
    for group in groups:
        for result in group:
            if result.chunk_id in seen:
                continue
            seen.add(result.chunk_id)
            union.append(result)
    return union


def _corrective_retrieve_global_rerank(state: AnswerState, policy: AnswerPolicy) -> AnswerState:
    """Phase 5 CP2 corrective mode: union candidate discovery (pass-1 pre-rerank
    fused pool + per-facet fused-order retrieval) followed by exactly one pass-2
    rerank against the original question, then the serving selection tail
    reused verbatim (edge/parent/sibling expansion -> dedup -> adaptive select).

    See docs/retrieval_strategy_review.md, "Phase 5 plan", design decisions 1-6.
    """
    from app.retriever.context_selection import (
        _snapshot_results,
        accepted_legal_query,
        select_post_rerank,
    )

    baseline_selected = state.selection.selected
    baseline_count = len(baseline_selected)
    baseline_ids = {result.chunk_id for result in baseline_selected}
    state.corrective_baseline_selected_count = baseline_count
    state.corrective_post_selected_count = baseline_count
    state.corrective_displaced_baseline_count = 0

    missing_facets = state.evidence.missing_facets if state.evidence else []
    if not missing_facets:
        state.corrective_ran = False
        state.corrective_added_chunks = 0
        return state

    max_facets = policy.corrective_max_facets or 0
    reserve_n = policy.corrective_facet_reserve_n or 0
    facets = missing_facets[:max_facets] if max_facets > 0 else []

    question = state.effective_question or state.question
    # Mirror select_context's exact accepted-legal-rewrite derivation (Phase 5
    # anti-pattern 6): pass 1 and pass 2 must see the identical structural
    # signal input, including the current accepted_legal_rewrite = legal_query
    # is not None behavior — not "fixed" to accepted-only here.
    legal_query = accepted_legal_query(state.legal_rewrite_decision)
    # Phase 5 design decision 2: the union base is the pass-1 pre-rerank fused
    # pool. SelectionResult.retrieved already holds exactly that pool, as a
    # clone-based snapshot (_snapshot_results, no score mutation) — no new
    # seam was needed.
    pre_rerank_pool = state.selection.retrieved

    facet_candidates: list[RetrievalResult] = []
    for facet_index, facet in enumerate(facets, start=1):
        query = _targeted_query(facet, question)
        # Phase 5 design decision 3: fused (RRF) order only, no per-facet
        # rerank call. The sole rerank invocation in this path is pass 2 below.
        retrieved = hybrid_retriever(
            query,
            knobs=state.strategy_knobs,
            query_variant="facet",
            query_ordinal=facet_index,
        )[:reserve_n]
        capture_candidates(
            "corrective_facet",
            retrieved,
            query_variant="facet",
            query_text=query,
            query_ordinal=facet_index,
            selected_ids={result.chunk_id for result in retrieved},
        )
        facet_candidates.extend(retrieved)

    union = _union_by_chunk_id(pre_rerank_pool, facet_candidates)
    capture_candidates(
        "corrective_union",
        union,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        pool_role="corrective_union_pool",
        selected_ids={result.chunk_id for result in union},
    )

    retrieved_trace = _snapshot_results(union)
    with stage_timer("corrective_rerank", in_n=len(union)) as stage:
        pre_expansion = rerank(
            question,
            union,
            knobs=state.strategy_knobs,
            query_variant="original",
            query_ordinal=0,
        )
        stage["out_n"] = len(pre_expansion)

    # Phase 5 design decision 4: reuse the serving post-rerank pipeline
    # verbatim (edge/parent/sibling expansion -> dedup_results -> adaptive
    # select) instead of a bespoke pass-2 merge.
    pass2 = select_post_rerank(
        question,
        pre_expansion,
        state.strategy_knobs,
        legal_query,
        retrieved_trace,
    )

    state.selection = pass2
    final_ids = {result.chunk_id for result in pass2.selected}
    displaced = baseline_ids - final_ids
    state.corrective_ran = True
    state.corrective_added_chunks = len(final_ids - baseline_ids)
    state.corrective_post_selected_count = len(pass2.selected)
    state.corrective_displaced_baseline_count = len(displaced)
    capture_candidates(
        "corrective",
        pass2.selected,
        query_variant="original",
        query_text=question,
        query_ordinal=0,
        selected_ids=final_ids,
    )
    return state
