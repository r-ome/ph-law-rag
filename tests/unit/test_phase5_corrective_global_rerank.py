"""Phase 5 CP2 — corrective global-rerank mechanism + knob/comparator plumbing.

docs/retrieval_strategy_review.md, "# Phase 5 plan" > "## Checkpoints" > CP2.
Mechanism-only: no paid calls, no eval runs — the CP1 cache is replayed or
exercised fail-closed via synthetic cache files.
"""

from dataclasses import replace

import pytest

from app.config import settings
from app.evals import retrieval_comparison as rc
from app.pipeline import evidence
from app.pipeline.corrective import corrective_retrieve
from app.pipeline.policy import AnswerPolicy
from app.pipeline.state import AnswerState, EvidenceReport
from app.retriever import facet_checker
from app.retriever.context_selection import SelectionResult
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit

_LONG_TEXT = (
    "Whoever, being a public officer, commits estafa through falsification of "
    "a public document shall suffer the penalty of prision correccional in "
    "its medium and maximum periods, unless a heavier penalty is provided."
)


def _result(chunk_id: str, text: str | None = None, **metadata) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        text=text or f"text {chunk_id}",
        score=1.0,
        metadata={
            "source_id": "rpc",
            "doc_id": "rpc-doc",
            "title": "Revised Penal Code",
            "url": "https://example.test/rpc",
            "provision_id": metadata.pop("provision_id", f"rpc:{chunk_id}"),
            "structure_path": "BOOK II",
            "unit_type": "article",
            "unit_label": chunk_id,
            **metadata,
        },
    )


def _policy(**overrides) -> AnswerPolicy:
    return replace(AnswerPolicy.from_settings(), **overrides)


def _inert_knobs(**overrides):
    base = AnswerPolicy.from_settings().retrieval_defaults
    fields = {
        "edge_expansion_enabled": False,
        "parent_expansion_enabled": False,
        "prefer_operative_enabled": False,
        "sibling_expansion_enabled": False,
        "consolidated_dedup_enabled": False,
        "adaptive_context_enabled": True,
        **overrides,
    }
    return replace(base, **fields)


def _state(
    *, pool, baseline_selected, missing_facets, knobs=None
) -> tuple[AnswerState, AnswerPolicy]:
    knobs = knobs or _inert_knobs()
    policy = _policy(
        corrective_mode="global_rerank",
        corrective_max_facets=3,
        corrective_facet_reserve_n=5,
        retrieval_defaults=knobs,
    )
    state = AnswerState(question="What is the penalty?", debug_enabled=False)
    state.strategy_knobs = knobs
    state.selection = SelectionResult(
        retrieved=pool, pre_expansion=pool, selected=baseline_selected
    )
    state.evidence = EvidenceReport(
        verdict="partial" if missing_facets else "sufficient",
        method="crag_facets",
        missing_facets=missing_facets,
        detail={},
    )
    return state, policy


def _identity_rerank(question, results, **kwargs):
    return list(results)


# ---------------------------------------------------------------------------
# 1. sufficient / empty missing_facets / append mode => pass 2 skipped.
# ---------------------------------------------------------------------------


def test_global_rerank_skips_pass2_when_missing_facets_empty(monkeypatch):
    baseline = SelectionResult(
        retrieved=[_result("pool1")],
        pre_expansion=[_result("pool1")],
        selected=[_result("sel1")],
    )
    state, policy = _state(pool=baseline.retrieved, baseline_selected=baseline.selected, missing_facets=[])
    state.selection = baseline

    def boom(*args, **kwargs):
        raise AssertionError("must not retrieve/rerank when missing_facets is empty")

    monkeypatch.setattr("app.pipeline.corrective.hybrid_retriever", boom)
    monkeypatch.setattr("app.pipeline.corrective.rerank", boom)

    result_state = corrective_retrieve(state, policy)

    assert result_state.corrective_ran is False
    assert result_state.corrective_added_chunks == 0
    assert result_state.selection is baseline
    assert result_state.selection.selected == baseline.selected


def test_append_mode_dispatches_to_legacy_path_unaffected(monkeypatch):
    """corrective_mode='append' (default) must never touch the global_rerank path."""
    state, policy = _state(
        pool=[_result("pool1")], baseline_selected=[_result("sel1")], missing_facets=[]
    )
    policy = replace(policy, corrective_mode="append")

    def boom(*args, **kwargs):
        raise AssertionError("global_rerank helper must not run in append mode")

    monkeypatch.setattr(
        "app.pipeline.corrective._corrective_retrieve_global_rerank", boom
    )
    # append mode with empty missing_facets is itself a no-op (legacy behavior).
    result_state = corrective_retrieve(state, policy)
    assert result_state.corrective_ran is False


# ---------------------------------------------------------------------------
# 2. Exactly one rerank invocation over the union.
# ---------------------------------------------------------------------------


def test_global_rerank_calls_rerank_exactly_once_over_union(monkeypatch):
    pool = [_result("pool1"), _result("pool2")]
    facet_candidates = [_result("facet1"), _result("facet2")]
    state, policy = _state(
        pool=pool, baseline_selected=[_result("sel1")], missing_facets=["penalty"]
    )

    monkeypatch.setattr(
        "app.pipeline.corrective.hybrid_retriever", lambda query, **kw: facet_candidates
    )
    rerank_calls: list[list[RetrievalResult]] = []

    def fake_rerank(question, results, **kwargs):
        rerank_calls.append(list(results))
        return list(results)

    monkeypatch.setattr("app.pipeline.corrective.rerank", fake_rerank)

    result_state = corrective_retrieve(state, policy)

    assert len(rerank_calls) == 1
    assert {r.chunk_id for r in rerank_calls[0]} == {"pool1", "pool2", "facet1", "facet2"}
    assert result_state.corrective_ran is True


def test_global_rerank_reranker_input_is_ordered_chunk_id_union_with_pool_precedence(monkeypatch):
    """CP3 precondition: assert the exact pass-2 reranker sequence, not a set."""
    pool = [_result("pool1"), _result("duplicate", text="pool copy"), _result("pool2")]
    facets = [_result("duplicate", text="facet copy"), _result("facet1"), _result("facet2")]
    state, policy = _state(
        pool=pool, baseline_selected=[], missing_facets=["penalty"]
    )
    monkeypatch.setattr(
        "app.pipeline.corrective.hybrid_retriever", lambda query, **kw: facets
    )
    calls = []

    def record_rerank(question, results, **kwargs):
        calls.append(list(results))
        return list(results)

    monkeypatch.setattr("app.pipeline.corrective.rerank", record_rerank)

    corrective_retrieve(state, policy)

    assert [[result.chunk_id for result in call] for call in calls] == [
        ["pool1", "duplicate", "pool2", "facet1", "facet2"]
    ]
    assert calls[0][1].text == "pool copy"


# ---------------------------------------------------------------------------
# 3. Union dedup: chunk_id + exact-text collapse, sibling leaves survive.
# ---------------------------------------------------------------------------


def test_union_dedup_collapses_chunk_id_and_exact_text_but_keeps_sibling_leaves(monkeypatch):
    pool = [
        _result("keep1", text=_LONG_TEXT, provision_id="rpc:sec1"),
        _result("dupchunk", text="pool version of dupchunk"),
    ]
    facet_candidates = [
        # Same chunk_id as a pool entry: union must keep the pool's instance
        # (first-seen) and drop this one — chunk_id dedup.
        _result("dupchunk", text="facet version of dupchunk (should not survive)"),
        # Exact-text duplicate of keep1, same provision, not a sibling: dropped
        # by dedup_results' near-duplicate rule.
        _result("neardupe", text=_LONG_TEXT, provision_id="rpc:sec1"),
        # Same provision + near-duplicate text (dedup_results' near-duplicate
        # rule would ordinarily drop it) but explicitly a sibling leaf: must
        # survive per dedup_results' sibling exemption (Phase 5 design
        # decision 5). Distinct enough from keep1 (suffix clause) that Phase
        # 4's exact-text adaptive dedup does not separately collapse it.
        _result(
            "siblingleaf",
            text=_LONG_TEXT
            + " This sibling article covers a related but distinct qualifying circumstance.",
            provision_id="rpc:sec1",
            expanded_from_sibling=True,
        ),
    ]
    knobs = _inert_knobs(consolidated_dedup_enabled=True)
    state, policy = _state(
        pool=pool, baseline_selected=[], missing_facets=["penalty"], knobs=knobs
    )

    monkeypatch.setattr(
        "app.pipeline.corrective.hybrid_retriever", lambda query, **kw: facet_candidates
    )
    monkeypatch.setattr("app.pipeline.corrective.rerank", _identity_rerank)

    result_state = corrective_retrieve(state, policy)
    final_ids = {r.chunk_id for r in result_state.selection.selected}

    assert "keep1" in final_ids
    assert "siblingleaf" in final_ids
    assert "neardupe" not in final_ids
    assert list(final_ids).count("dupchunk") <= 1
    assert "dupchunk" in final_ids
    kept_dupchunk = next(r for r in result_state.selection.selected if r.chunk_id == "dupchunk")
    assert kept_dupchunk.text == "pool version of dupchunk"


# ---------------------------------------------------------------------------
# 4. Pass-2 runs expansion (a facet candidate whose sibling qualifies expands).
# ---------------------------------------------------------------------------


def test_pass2_runs_sibling_expansion_for_a_facet_candidate(monkeypatch):
    pool = [_result("pool1")]
    facet_candidates = [_result("facetseed")]
    knobs = _inert_knobs(sibling_expansion_enabled=True)
    state, policy = _state(
        pool=pool, baseline_selected=[], missing_facets=["penalty"], knobs=knobs
    )

    monkeypatch.setattr(
        "app.pipeline.corrective.hybrid_retriever", lambda query, **kw: facet_candidates
    )
    monkeypatch.setattr("app.pipeline.corrective.rerank", _identity_rerank)

    def fake_expand_siblings(results, knobs=None):
        if any(r.chunk_id == "facetseed" for r in results):
            return [*results, _result("facetsibling", expanded_from_sibling=True)]
        return results

    monkeypatch.setattr(
        "app.retriever.sibling_expansion.expand_siblings", fake_expand_siblings
    )

    result_state = corrective_retrieve(state, policy)
    final_ids = {r.chunk_id for r in result_state.selection.selected}

    assert "facetseed" in final_ids
    assert "facetsibling" in final_ids


# ---------------------------------------------------------------------------
# 5. Displaced-baseline count traced correctly.
# ---------------------------------------------------------------------------


def test_displaced_baseline_count_traced(monkeypatch):
    baseline_selected = [_result("keepbaseline"), _result("droppedbaseline")]
    pool = [_result("keepbaseline"), _result("poolX")]
    facet_candidates = [_result("facetY")]
    state, policy = _state(
        pool=pool, baseline_selected=baseline_selected, missing_facets=["penalty"]
    )

    monkeypatch.setattr(
        "app.pipeline.corrective.hybrid_retriever", lambda query, **kw: facet_candidates
    )
    monkeypatch.setattr("app.pipeline.corrective.rerank", _identity_rerank)

    result_state = corrective_retrieve(state, policy)
    final_ids = {r.chunk_id for r in result_state.selection.selected}

    assert final_ids == {"keepbaseline", "poolX", "facetY"}
    assert result_state.corrective_displaced_baseline_count == 1
    assert result_state.corrective_added_chunks == 2
    assert result_state.corrective_post_selected_count == 3
    assert result_state.corrective_baseline_selected_count == 2


# ---------------------------------------------------------------------------
# 6. Checker-call caching: fail-closed miss, zero-network hit.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    yield


def test_cached_checker_cache_miss_fails_closed():
    chunks = [_result("c1")]
    with pytest.raises(RuntimeError, match="cache miss"):
        evidence._check_crag_facets_cached(
            "What is the penalty?",
            chunks,
            model="claude-haiku-4-5",
            row_label="eval_999",
            authorize_paid_calls=False,
        )


def test_cached_checker_cache_hit_makes_zero_network_calls(monkeypatch):
    question = "What is the penalty?"
    chunks = [_result("c1", text="Some legal passage text about penalties.")]
    rendered = facet_checker._render_crag_prompt(question, chunks)

    monkeypatch.setattr(
        facet_checker,
        "_call_haiku",
        lambda prompt, model: (
            "FACETS: penalty\nPRESENT: penalty\nMISSING: none\nVERDICT: sufficient"
        ),
    )
    facet_checker.call_and_cache(rendered, model="claude-haiku-4-5")

    def boom(*args, **kwargs):
        raise AssertionError("no network call expected on a cache hit")

    monkeypatch.setattr(facet_checker, "_call_haiku", boom)

    verdict, missing, detail = evidence._check_crag_facets_cached(
        question,
        chunks,
        model="claude-haiku-4-5",
        row_label="eval_1",
        authorize_paid_calls=False,
    )
    assert verdict == "sufficient"
    assert missing == []
    assert detail["cache_status"] == "hit"


def test_evaluate_evidence_routes_crag_gate_through_cache_for_global_rerank_mode(monkeypatch):
    question = "What is the penalty?"
    selected = [_result("sel1", text="Selected passage text.")]
    pre_expansion = [_result("pe1", text="Pre-expansion passage text.")]
    rendered = facet_checker._render_crag_prompt(question, selected)
    monkeypatch.setattr(
        facet_checker,
        "_call_haiku",
        lambda prompt, model: "FACETS: x\nPRESENT: x\nMISSING: none\nVERDICT: sufficient",
    )
    facet_checker.call_and_cache(rendered, model="claude-haiku-4-5")

    state = AnswerState(question=question, debug_enabled=False)
    state.selection = SelectionResult(
        retrieved=pre_expansion, pre_expansion=pre_expansion, selected=selected
    )
    policy = _policy(
        evidence_gate="crag",
        evidence_judge_model="claude-haiku-4-5",
        corrective_mode="global_rerank",
        corrective_max_facets=3,
        corrective_facet_reserve_n=5,
        min_chunks_for_answer=1,
    )

    report = evidence.evaluate_evidence(state, policy, authorize_paid_calls=False)

    assert report.verdict == "sufficient"


# ---------------------------------------------------------------------------
# 7. global_rerank + adaptive_context_enabled=False => policy-resolution error.
# ---------------------------------------------------------------------------


def test_global_rerank_requires_adaptive_context_at_policy_resolution():
    base = AnswerPolicy.from_settings()
    disabled_knobs = replace(base.retrieval_defaults, adaptive_context_enabled=False)
    with pytest.raises(ValueError, match="global_rerank"):
        replace(base, corrective_mode="global_rerank", retrieval_defaults=disabled_knobs)


def test_append_mode_with_adaptive_context_disabled_is_allowed():
    base = AnswerPolicy.from_settings()
    disabled_knobs = replace(base.retrieval_defaults, adaptive_context_enabled=False)
    # No error: append mode never needed the anti-degrade guard.
    replace(base, corrective_mode="append", retrieval_defaults=disabled_knobs)


def test_global_rerank_requires_subquery_packaging_disabled_at_policy_resolution():
    """Under subquery packaging, SelectionResult.retrieved holds packaged_retrieve's
    per-subquery output, not the pre-rerank fused pool design decision 2 requires
    as the union base — packaging + global_rerank must fail at construction."""
    base = AnswerPolicy.from_settings()
    packaged_knobs = replace(base.retrieval_defaults, subquery_packaging_enabled=True)
    with pytest.raises(ValueError, match="subquery_packaging_enabled"):
        replace(base, corrective_mode="global_rerank", retrieval_defaults=packaged_knobs)


def test_append_mode_with_subquery_packaging_enabled_is_allowed():
    base = AnswerPolicy.from_settings()
    packaged_knobs = replace(base.retrieval_defaults, subquery_packaging_enabled=True)
    # No error: append mode never read SelectionResult.retrieved as a union base.
    replace(base, corrective_mode="append", retrieval_defaults=packaged_knobs)


# ---------------------------------------------------------------------------
# 8. Comparator declared-delta set-equality (both directions of failure).
# ---------------------------------------------------------------------------


_DECLARED = {
    "evidence_gate": ("min_chunks", "crag"),
    "corrective_retrieval_enabled": (False, True),
    "corrective_mode": ("append", "global_rerank"),
    "evidence_judge_model": ("mistral", "claude-haiku-4-5"),
    "corrective_max_facets": (None, 3),
    "corrective_facet_reserve_n": (None, 5),
}


def _matched_arm_shared_values():
    baseline, _ = rc._comparable_shared_values(
        {
            "retrieval_defaults": {},
            "evidence_gate": "min_chunks",
            "evidence_judge_model": "mistral",
            "corrective_retrieval_enabled": False,
        }
    )
    candidate, _ = rc._comparable_shared_values(
        {
            "retrieval_defaults": {},
            "evidence_gate": "crag",
            "evidence_judge_model": "claude-haiku-4-5",
            "corrective_retrieval_enabled": True,
            "corrective_mode": "global_rerank",
            "corrective_max_facets": 3,
            "corrective_facet_reserve_n": 5,
        }
    )
    rc._align_inactive_adaptive_context(baseline, candidate)
    return baseline, candidate


def test_comparator_declared_delta_matches_exactly():
    baseline, candidate = _matched_arm_shared_values()
    observed = rc._selection_diff(baseline, candidate)
    declared = rc._normalize_expected_knob_diff(_DECLARED)

    assert observed == declared
    rc._require_expected_knob_diff(observed, declared)  # must not raise


def test_comparator_rejects_missing_declared_delta():
    """A declared delta that isn't actually observed fails as 'unobserved'."""
    baseline, candidate = _matched_arm_shared_values()
    observed = rc._selection_diff(baseline, candidate)
    over_declared = dict(_DECLARED)
    over_declared["subquery_packaging_enabled"] = (False, True)  # not an actual diff
    declared = rc._normalize_expected_knob_diff(over_declared)

    with pytest.raises(ValueError, match="unobserved"):
        rc._require_expected_knob_diff(observed, declared)


def test_comparator_rejects_undeclared_extra_delta():
    baseline, candidate = _matched_arm_shared_values()
    candidate = dict(candidate)
    candidate["retrieval_defaults"] = {
        **candidate["retrieval_defaults"],
        "sibling_expansion_enabled": True,
    }
    observed = rc._selection_diff(baseline, candidate)
    declared = rc._normalize_expected_knob_diff(_DECLARED)

    with pytest.raises(ValueError, match="undeclared"):
        rc._require_expected_knob_diff(observed, declared)


# ---------------------------------------------------------------------------
# 9. CP3 sealed-comparator rejection gates.
# ---------------------------------------------------------------------------


def _cp3_row(eval_id, *, fired=False, tokens=1000, unit_label="Article 45(3)"):
    stages = [{"name": "adaptive_context", "fields": {"rendered_tokens": tokens}}]
    if fired:
        stages.append({"name": "adaptive_context", "fields": {"rendered_tokens": tokens}})
    return {
        "eval_id": eval_id,
        "selected_results": [{"metadata": {
            "source_id": "family_code", "provision_id": "family_code:article:45",
            "unit_label": unit_label,
        }}],
        "corrective_retrieval": {"ran": fired},
        "selected_context_hash": "selected-" + eval_id,
        "context_block_hash": "context-" + eval_id,
        "source_map_hash": "sources-" + eval_id,
        "system_prompt_hash": "system-" + eval_id,
        "user_prompt_hash": "user-" + eval_id,
        "retrieval_trace": {"stages": stages},
    }


def _patch_cp3_inputs(monkeypatch):
    targets = {
        "eval_fired": {
            "match_mode": "exact",
            "targets": [{
                "source_id": "family_code", "provision_id": "family_code:article:45",
                "unit_label": "Article 45(3)",
            }],
        },
        "eval_sufficient": {"match_mode": "source_only", "targets": []},
    }
    monkeypatch.setattr(rc, "_phase5_target_hash", lambda *args: (targets, "target-hash"))
    monkeypatch.setattr(rc, "_load_cp1_partial_ids", lambda *args, **kwargs: {"eval_fired"})


def _run_cp3_gates(monkeypatch, baseline, candidate):
    _patch_cp3_inputs(monkeypatch)
    return rc._phase5_cp3_gates(
        baseline,
        candidate,
        baseline_meta={},
        candidate_meta={"clean_worktree": {"clean": True, "git_sha": "abc123"}},
        audit_tag="unused",
    )


def test_cp3_rejects_leaf_target_lost_despite_same_provision(monkeypatch):
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [
        _cp3_row("eval_fired", fired=True, unit_label="Article 45(1)"),
        _cp3_row("eval_sufficient"),
    ]
    with pytest.raises(ValueError, match="fired_row_target_set_preservation"):
        _run_cp3_gates(monkeypatch, baseline, candidate)


def test_cp3_rejects_sufficient_prompt_hash_drift(monkeypatch):
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [_cp3_row("eval_fired", fired=True), _cp3_row("eval_sufficient")]
    candidate[1]["user_prompt_hash"] = "drifted"
    with pytest.raises(ValueError, match="sufficient_row_identity"):
        _run_cp3_gates(monkeypatch, baseline, candidate)


def test_cp3_rejects_missing_sufficient_identity_field(monkeypatch):
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [_cp3_row("eval_fired", fired=True), _cp3_row("eval_sufficient")]
    baseline[1].pop("source_map_hash")
    candidate[1].pop("source_map_hash")
    with pytest.raises(ValueError, match="sufficient_row_identity"):
        _run_cp3_gates(monkeypatch, baseline, candidate)


def test_cp3_rejects_missing_clean_worktree_provenance(monkeypatch):
    _patch_cp3_inputs(monkeypatch)
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [_cp3_row("eval_fired", fired=True), _cp3_row("eval_sufficient")]
    with pytest.raises(ValueError, match="clean-worktree provenance"):
        rc._phase5_cp3_gates(
            baseline, candidate, baseline_meta={}, candidate_meta={}, audit_tag="unused"
        )


def test_cp3_rejects_unexpected_firing_row(monkeypatch):
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [_cp3_row("eval_fired", fired=True), _cp3_row("eval_sufficient", fired=True)]
    candidate[1]["retrieval_trace"]["stages"].append(
        {"name": "adaptive_context", "fields": {"rendered_tokens": 1000}}
    )
    with pytest.raises(ValueError, match="expected_firing_population"):
        _run_cp3_gates(monkeypatch, baseline, candidate)


def test_cp3_rejects_context_bound_breach(monkeypatch):
    baseline = [_cp3_row("eval_fired"), _cp3_row("eval_sufficient")]
    candidate = [_cp3_row("eval_fired", fired=True, tokens=4000), _cp3_row("eval_sufficient")]
    with pytest.raises(ValueError, match="context_bounds"):
        _run_cp3_gates(monkeypatch, baseline, candidate)


@pytest.mark.parametrize("stage_count", [0, 3])
def test_cp3_rejects_invalid_direct_adaptive_stage_counts(stage_count):
    row = _cp3_row("eval_fired", fired=True)
    row["retrieval_trace"]["stages"] = [
        {"name": "adaptive_context", "fields": {"rendered_tokens": 1000}}
        for _ in range(stage_count)
    ]
    with pytest.raises(ValueError, match="expected exactly 2"):
        rc._final_rendered_tokens(row, fired=True)


def test_cp3_rejects_shape_without_adaptive_diagnostic():
    row = {"eval_id": "eval_999", "retrieval_trace": {"stages": []}}
    with pytest.raises(ValueError, match="expected exactly 1"):
        rc._final_rendered_tokens(row, fired=False)
