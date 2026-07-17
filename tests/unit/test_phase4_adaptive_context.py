import json
import math
import subprocess
import sys

import pytest

from app.config import settings
from app.evals.context_selection_replay import replay_context_selection
from app.evals.frozen_contexts import seal
from app.evals.integrity import (
    _pre_rerank_pool_hash,
    append_hashed_row,
    paths_for,
    query_separation_identity,
    retrieval_config_identity,
    schema_version,
    sha256,
    text_sha256,
    validate_sealed_bundle,
)
from app.evals import retrieval_comparison
from app.retriever.adaptive_context import (
    ADAPTIVE_CONTEXT_DEFAULTS,
    AdaptiveContextSignals,
    estimate_rendered_tokens,
    packaging_pool_full_hash,
    packaging_pool_semantic_hash,
    select_adaptive_context,
)
from app.retriever.context_builder import build_context
from app.retriever.prompts import SYSTEM_PROMPT, build_user_prompt
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _result(chunk_id, text=None, **metadata):
    return RetrievalResult(
        chunk_id=chunk_id,
        text=text or f"text {chunk_id}",
        score=100.0 - len(chunk_id),
        metadata={
            "source_id": "civil_code",
            "doc_id": "civil-doc",
            "title": "Civil Code",
            "url": "https://example.test/civil",
            "provision_id": f"civil:{chunk_id}",
            "structure_path": "BOOK IV > TITLE I",
            "unit_type": "article",
            "unit_label": chunk_id,
            **metadata,
        },
    )


def test_non_contiguous_sibling_bundle_fires_at_first_member_and_crosses_cap():
    results = [
        _result("a"),
        _result("b"),
        _result(
            "c",
            provision_id="civil:article:1403",
            parent_key="civil::article:1403",
            unit_label="Article 1403(2)(c)",
            expanded_from_sibling=True,
            sibling_seed_chunk_id="seed",
        ),
        _result(
            "seed",
            provision_id="civil:article:1403",
            parent_key="civil::article:1403",
            unit_label="Article 1403(2)(d)",
        ),
        _result(
            "e",
            provision_id="civil:article:1403",
            parent_key="civil::article:1403",
            unit_label="Article 1403(2)(e)",
            expanded_from_sibling=True,
            sibling_seed_chunk_id="seed",
        ),
        _result("later"),
    ]

    selected, diagnostics = select_adaptive_context(results, base_cap=4)

    assert [result.chunk_id for result in selected] == ["a", "b", "c", "seed", "e"]
    assert diagnostics.chunk_cap_overflow == 1
    assert diagnostics.stop_reason == "cap"


def test_dangling_seed_id_still_groups_surviving_siblings():
    results = [
        _result("a"),
        _result("b"),
        _result(
            "merged",
            dedup_merged_chunk_ids=["removed-seed"],
        ),
        _result("removed-seed"),
        _result(
            "left",
            parent_key="p",
            sibling_seed_chunk_id="removed-seed",
            expanded_from_sibling=True,
        ),
        _result(
            "right",
            parent_key="p",
            sibling_seed_chunk_id="removed-seed",
            expanded_from_sibling=True,
        ),
    ]

    selected, diagnostics = select_adaptive_context(results, base_cap=4)

    assert [result.chunk_id for result in selected] == [
        "a",
        "b",
        "merged",
        "left",
        "right",
    ]
    assert diagnostics.represented_chunks_removed == 1
    assert diagnostics.chunk_cap_overflow == 1


def test_defensive_dedup_is_limited_to_id_represented_and_exact_text():
    results = [
        _result("a", text="Alpha"),
        _result("a", text="other duplicate id"),
        _result("merged", dedup_merged_chunk_ids=["represented"]),
        _result("represented"),
        _result("exact", text="  ALPHA\n"),
        _result("distinct", text="Alpha plus"),
    ]

    selected, diagnostics = select_adaptive_context(results)

    assert [result.chunk_id for result in selected] == ["a", "merged", "distinct"]
    assert diagnostics.duplicate_chunk_ids_removed == 1
    assert diagnostics.represented_chunks_removed == 1
    assert diagnostics.duplicate_texts_removed == 1


def test_novelty_widens_uncertain_context_but_stabilization_stops_repetition():
    same = {
        "provision_id": "civil:same",
        "parent_key": "same-family",
        "unit_label": "same-leaf",
    }
    results = [_result(str(index), **same) for index in range(6)]
    selected, diagnostics = select_adaptive_context(
        results,
        signals=AdaptiveContextSignals(coverage_uncertain=True),
    )
    assert [result.chunk_id for result in selected] == ["0", "1", "2", "3"]
    assert diagnostics.stop_reason == "stabilized"
    assert diagnostics.non_novel_bundles == 2

    novel = [*_result_list(4, same), _result("new", source_id="family_code")]
    widened, _ = select_adaptive_context(
        novel,
        signals=AdaptiveContextSignals(coverage_uncertain=True),
    )
    assert [result.chunk_id for result in widened][-1] == "new"


def _result_list(count, metadata):
    return [_result(str(index), **metadata) for index in range(count)]


def test_rendered_token_estimator_ignores_stored_estimates_and_reports_soft_overflow():
    results = [
        _result(str(index), text=(str(index) + "x" * 300), token_estimate=1)
        for index in range(4)
    ]
    rendered, _ = build_context(results)
    assert estimate_rendered_tokens(results) == math.ceil(len(rendered) / 4)

    selected, diagnostics = select_adaptive_context(results, token_target=10)
    assert len(selected) >= 4
    assert diagnostics.token_overflow > 0
    assert diagnostics.stop_reason == "token_target"


def test_packaging_semantic_hash_ignores_selector_irrelevant_scores():
    baseline = [
        _result(
            "a",
            score=0.9,
            token_estimate=1,
            _retrieval_scores={"dense_score": 0.7406719},
        )
    ]
    drifted = [
        _result(
            "a",
            score=0.9,
            token_estimate=999,
            _retrieval_scores={"dense_score": 0.7406968},
        )
    ]

    assert packaging_pool_semantic_hash(baseline) == packaging_pool_semantic_hash(
        drifted
    )
    assert packaging_pool_full_hash(baseline) != packaging_pool_full_hash(drifted)


def test_adaptive_comparator_defaults_and_only_enabled_delta():
    assert ADAPTIVE_CONTEXT_DEFAULTS == {
        "adaptive_context_enabled": True,
        "adaptive_context_contract_version": 2,
        "adaptive_context_floor": 4,
        "adaptive_context_base_cap": 7,
        "adaptive_context_uncertain_cap": 11,
        "adaptive_context_multifacet_cap": 11,
        "adaptive_context_stabilization_patience": 2,
        "adaptive_context_token_target": 2400,
        "adaptive_context_token_estimator": "rendered_chars_div4_v1",
    }
    assert retrieval_comparison._LEGACY_ADAPTIVE_DEFAULTS[
        "adaptive_context_enabled"
    ] is False

    legacy = {"retrieval_defaults": {}}
    normalized, _ = retrieval_comparison._comparable_shared_values(legacy)
    for key, value in retrieval_comparison._LEGACY_ADAPTIVE_DEFAULTS.items():
        assert normalized["retrieval_defaults"][key] == value

    candidate, _ = retrieval_comparison._comparable_shared_values(legacy)
    candidate["retrieval_defaults"]["adaptive_context_enabled"] = True
    assert retrieval_comparison._selection_diff(normalized, candidate) == {
        "adaptive_context_enabled": (False, True)
    }


def test_comparator_uses_active_adaptive_contract_for_disabled_legacy_arm():
    baseline, _ = retrieval_comparison._comparable_shared_values(
        {"retrieval_defaults": {}}
    )
    candidate, _ = retrieval_comparison._comparable_shared_values(
        {
            "retrieval_defaults": {
                "adaptive_context_enabled": True,
                "adaptive_context_contract_version": 1,
                "adaptive_context_floor": 4,
                "adaptive_context_base_cap": 4,
                "adaptive_context_uncertain_cap": 6,
                "adaptive_context_multifacet_cap": 8,
                "adaptive_context_stabilization_patience": 2,
                "adaptive_context_token_target": 2048,
                "adaptive_context_token_estimator": "rendered_chars_div4_v1",
            }
        }
    )

    retrieval_comparison._align_inactive_adaptive_context(baseline, candidate)

    assert retrieval_comparison._selection_diff(baseline, candidate) == {
        "adaptive_context_enabled": (False, True)
    }
    for name in retrieval_comparison._ADAPTIVE_CONTEXT_INERT_KEYS:
        assert baseline["retrieval_defaults"][name] == candidate[
            "retrieval_defaults"
        ][name]


def test_comparator_ignores_adaptive_contract_when_both_arms_are_disabled():
    baseline, _ = retrieval_comparison._comparable_shared_values(
        {"retrieval_defaults": {}}
    )
    candidate, _ = retrieval_comparison._comparable_shared_values(
        {
            "retrieval_defaults": {
                "adaptive_context_contract_version": 1,
                "adaptive_context_base_cap": 4,
                "adaptive_context_uncertain_cap": 6,
                "adaptive_context_multifacet_cap": 8,
                "adaptive_context_token_target": 2048,
            }
        }
    )

    retrieval_comparison._align_inactive_adaptive_context(baseline, candidate)

    assert retrieval_comparison._selection_diff(baseline, candidate) == {}
    for name in retrieval_comparison._ADAPTIVE_CONTEXT_INERT_KEYS:
        assert name not in baseline["retrieval_defaults"]
        assert name not in candidate["retrieval_defaults"]


def test_replay_module_does_not_import_model_or_retrieval_services():
    script = """
import sys
import app.evals.context_selection_replay
for name in (
    'app.retriever.hybrid_retriever',
    'app.retriever.reranker',
    'app.retriever.legal_query_rewriter',
    'app.retriever.llm_client',
):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def _frozen_result(result):
    return {
        "chunk_id": result.chunk_id,
        "text": result.text,
        "score": result.score,
        "metadata": result.metadata,
    }


def _legal_separation(question):
    identity = query_separation_identity()
    decision = {
        "status": "disabled",
        "legal_query": None,
        "legal_query_hash": None,
        "confidence": None,
        "parser_outcome": "not_called",
        "fallback_reason": "disabled",
        "model": None,
        "prompt_version": identity["prompt_version"],
        "prompt_hash": identity["prompt_hash"],
        "raw_output_hash": None,
        "call_latency_ms": None,
        "cache_key": None,
        "cache_status": "bypassed",
    }
    semantic = {k: v for k, v in decision.items() if k not in {"call_latency_ms", "cache_status"}}
    return {
        "arm": "original_only",
        "source_query": question,
        "source_query_hash": text_sha256(question),
        "decision": decision,
        "semantic_input_hash": sha256(
            {
                "arm": "original_only",
                "source_query": question,
                "source_query_hash": text_sha256(question),
                "decision": semantic,
            }
        ),
    }


def _write_source_bundle(tag, *, split="dev"):
    question = "What is the rule?"
    results = [_result(str(index), provision_id="civil:same", unit_label="same") for index in range(5)]
    selected = [_frozen_result(result) for result in results]
    fused = {
        "stage": "fused",
        "pool_role": "pre_rerank_pool",
        "query_variant": "combined",
        "candidates": [
            {"rank": index + 1, **item} for index, item in enumerate(selected)
        ],
    }
    terminal = {
        "stage": "selected",
        "query_variant": "original",
        "candidates": [
            {"rank": index + 1, "selected": True, "survived": True, **item}
            for index, item in enumerate(selected)
        ],
    }
    candidate_stages = [fused, terminal]
    context, sources = build_context(results)
    user = build_user_prompt(question, context)
    row = {
        "schema": schema_version(),
        "eval_id": "eval_x",
        "question": question,
        "effective_question": question,
        "selection": {
            "retrieved": selected,
            "pre_expansion": selected,
            "selected": selected,
        },
        "selected_results": selected,
        "selected_context_hash": sha256(selected),
        "pre_rerank_pool_hash": _pre_rerank_pool_hash(candidate_stages, schema_minor=1),
        "candidate_stages": candidate_stages,
        "legal_query_separation": _legal_separation(question),
        "retrieval_trace": {"retrieval_latency_ms": 1.0, "retrieval_stage_timings_ms": {}},
        "evidence": {
            "method": "min_chunks",
            "verdict": "sufficient",
            "missing_facets": [],
            "detail": {"min_chunks_for_answer": 1, "pre_expansion_count": 5, "selected_count": 5},
        },
        "corrective_retrieval": {"ran": False, "added_chunks": 0},
        "terminal_response": None,
        "hard_abstention": False,
        "model_choice": {"model": "local", "reason": "policy_default"},
        "policy": {"later_enacted_preference_enabled": False, "retrieval_defaults": {}},
        "context_block_hash": text_sha256(context),
        "source_map": sources,
        "source_map_hash": sha256(sources),
        "system_prompt_hash": text_sha256(SYSTEM_PROMPT),
        "user_prompt_hash": text_sha256(user),
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user,
        "category": "factual",
        "split": split,
        "retrieval_target_present": True,
    }
    paths = paths_for(tag, create=True)
    hashed = append_hashed_row(paths.partial, row)
    shared = {"profile": "eval", "retrieval_defaults": {}}
    meta = {
        "tag": tag,
        "splits": [split],
        "holdout": split == "holdout",
        "dataset_identity": {"hash": "dataset"},
        "targets_identity": {"hash": "targets"},
        "corpus_identity": {"hash": "corpus"},
        "index_identity": {"hash": "index"},
        "retrieval_config": retrieval_config_identity(shared),
    }
    target = {
        "eval_id": "eval_x",
        "match_mode": "exact",
        "targets": [{"source_id": "civil_code", "provision_id": "civil:same"}],
    }
    seal(paths, meta=meta, rows=[hashed], targets_by_id={"eval_x": target})
    return paths


def test_replay_stays_schema_11_recomputes_and_seals(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    source = _write_source_bundle("source")
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            "eval_x": {
                "eval_id": "eval_x",
                "match_mode": "exact",
                "targets": [{"source_id": "civil_code", "provision_id": "civil:same"}],
            }
        },
    )

    output = replay_context_selection("source", tag="adaptive", selector="adaptive")
    rows, meta = validate_sealed_bundle(paths_for("adaptive"))

    assert output == paths_for("adaptive").sealed
    assert meta["schema"]["minor"] == 1
    assert len(rows[0]["selected_results"]) == 4
    assert rows[0]["selection"]["pre_expansion"] == json.loads(source.sealed.read_text().splitlines()[0])["selection"]["pre_expansion"]
    assert rows[0]["evidence"]["detail"]["selected_count"] == 4
    assert rows[0]["adaptive_context"]["source_selected_context_hash"]
    assert rows[0]["pre_rerank_pool_hash"] == json.loads(source.sealed.read_text().splitlines()[0])["pre_rerank_pool_hash"]
    assert meta["retrieval_config"]["shared_values"]["retrieval_defaults"]["adaptive_context_enabled"] is True
    assert meta["adaptive_context_experiment"]["changed_rows"] == 1
    assert meta["adaptive_context_experiment"]["anti_inert_passed"] is True
    assert "mean_reduction_watch_triggered" in meta["adaptive_context_experiment"]


def test_replay_rejects_holdout_and_tag_reuse_before_output(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_source_bundle("holdout-source", split="holdout")
    with pytest.raises(ValueError, match="holdout"):
        replay_context_selection("holdout-source", tag="must-not-exist")
    assert not paths_for("must-not-exist").root.exists()

    _write_source_bundle("source")
    paths_for("used", create=True)
    with pytest.raises(FileExistsError, match="already exists"):
        replay_context_selection("source", tag="used")


def test_replay_rejects_an_already_adaptive_source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_source_bundle("source")
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            "eval_x": {
                "eval_id": "eval_x",
                "match_mode": "exact",
                "targets": [
                    {"source_id": "civil_code", "provision_id": "civil:same"}
                ],
            }
        },
    )

    replay_context_selection("source", tag="adaptive", selector="adaptive")
    with pytest.raises(ValueError, match="fixed-control packaging pool"):
        replay_context_selection("adaptive", tag="double-adaptive", selector="adaptive")
    assert not paths_for("double-adaptive").root.exists()


def test_fixed_and_adaptive_replays_compare_with_only_enabled_delta(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_source_bundle("source")
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            "eval_x": {
                "eval_id": "eval_x",
                "match_mode": "exact",
                "targets": [
                    {"source_id": "civil_code", "provision_id": "civil:same"}
                ],
            }
        },
    )

    replay_context_selection("source", tag="fixed", selector="fixed")
    replay_context_selection("source", tag="adaptive", selector="adaptive")
    report_path = retrieval_comparison.compare_retrieval_bundles(
        "fixed",
        "adaptive",
        tag="comparison",
        expected_arm_pair=("original_only", "original_only"),
        expected_knob_diff={"adaptive_context_enabled": (False, True)},
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {
        "row_count": 1,
        "pre_rerank_pool_changed": 0,
        "selected_context_changed": 1,
    }
    assert report["observed_knob_diff"] == {
        "adaptive_context_enabled": {"baseline": False, "candidate": True}
    }
