import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.config import settings
from app.evals.integrity import (
    _pre_rerank_pool_hash,
    append_hashed_row,
    atomic_write_json,
    file_sha256,
    ordered_hash,
    paths_for,
    read_hashed_rows,
    retrieval_config_identity,
    schema_version,
    sha256,
    validate_schema,
    validate_sealed_bundle,
)
from app.evals.retrieval_comparison import compare_retrieval_bundles
from app.evals.retrieval_metrics import build_retrieval_summary
from app.evals.retrieval_trace import (
    append_completed_row,
    candidate_count_metadata,
    candidate_lines,
)
from app.pipeline.policy import resolve_policy
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _candidate(chunk_id: str = "chunk-1", text: str = "law", *, source: str = "s"):
    return {
        "rank": 1,
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.5,
        "fused_score": 0.5,
        "metadata": {
            "source_id": source,
            "provision_id": f"{source}:section:1",
            "unit_label": "Section 1",
        },
    }


def _candidate_stages(minor: int, *, pool_text: str = "law") -> list[dict]:
    candidate = _candidate(text=pool_text)
    if minor == 0:
        return [{"stage": "fused", "query_variant": "original", "candidates": [candidate]}]
    return [
        {"stage": "dense", "query_variant": "original", "candidates": [candidate]},
        {"stage": "sparse", "query_variant": "original", "candidates": [candidate]},
        {"stage": "fused", "query_variant": "original", "candidates": [candidate]},
        {
            "stage": "fused",
            "query_variant": "combined",
            "pool_role": "pre_rerank_pool",
            "candidates": [deepcopy(candidate)],
        },
        {"stage": "reranked", "query_variant": "original", "candidates": [candidate]},
        {"stage": "expanded", "query_variant": "original", "candidates": [candidate]},
        {"stage": "selected", "query_variant": "original", "candidates": [candidate]},
    ]


def _shared_values() -> dict:
    return {
        "profile": "eval",
        "retrieval_defaults": {
            "dense_top_k": 30,
            "sparse_top_k": 10,
            "sparse_overfetch_k": 100,
            "rerank_top_n": 8,
            "rerank_score_margin": 6.0,
            "max_distance": 0.5,
            "edge_expansion_enabled": True,
            "edge_hop_top_k": 3,
            "parent_expansion_enabled": True,
            "parent_expansion_min_children": 2,
            "parent_expansion_max_chars": 8000,
            "prefer_operative_enabled": False,
            "retrieval_operative_only": True,
            "consolidated_dedup_enabled": True,
            "query_decomposition_enabled": False,
            "query_planner_model": "mistral",
            "query_planner_max_subqueries": 3,
            "subquery_packaging_enabled": False,
            "subquery_reserve_n": 2,
        },
        "query_decomposition_enabled": False,
        "query_rewriting_enabled": True,
        "evidence_gate": "min_chunks",
        "evidence_judge_model": "gemma4:e4b",
        "min_chunks_for_answer": 1,
        "corrective_retrieval_enabled": False,
        "embedding_backend": "ollama",
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_dim": 1024,
        "embedding_query_instruction": "legal",
        "reranker_backend": "minilm",
        "reranker_model": "minilm",
        "qwen3_reranker_model": "qwen3",
        "bedrock_rerank_model": "bedrock",
        "qdrant_collection": "ph_law",
        "chunk_size": 256,
        "chunk_overlap": 32,
    }


def _write_bundle(
    tag: str,
    *,
    minor: int,
    arm: str = "original_only",
    candidate_stages: list[dict] | None = None,
    retrieval_config: dict | None = None,
    pool_text: str = "law",
    selected_text: str = "selected law",
    holdout: bool = False,
    dataset_identity: dict | None = None,
    corpus_hash: str = "corpus",
    compute_pool_hash: bool = True,
):
    schema = schema_version(minor=minor)
    stages = candidate_stages if candidate_stages is not None else _candidate_stages(
        minor, pool_text=pool_text
    )
    selected = [
        {
            "chunk_id": "selected-1",
            "text": selected_text,
            "score": 1.0,
            "metadata": {"source_id": "s", "provision_id": "s:section:1"},
        }
    ]
    pre_hash = (
        _pre_rerank_pool_hash(stages, schema_minor=minor)
        if compute_pool_hash
        else None
    )
    record = {
        "schema": schema,
        "eval_id": "eval_001",
        "question": "question",
        "candidate_stages": stages,
        "pre_rerank_pool_hash": pre_hash,
        "selected_results": selected,
        "selected_context_hash": sha256(selected),
    }
    paths = paths_for(tag, datetime(2026, 7, 15).astimezone(), create=True)
    written = append_hashed_row(paths.partial, record)
    os.replace(paths.partial, paths.sealed)
    paths.trace.write_text("", encoding="utf-8")
    paths.summary.write_text("{}\n", encoding="utf-8")
    publication = {
        "row_count": 1,
        "eval_ids": ["eval_001"],
        "ordered_record_hash": ordered_hash([written["record_hash"]]),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [{"eval_id": "eval_001", "hash": pre_hash}]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [{"eval_id": "eval_001", "hash": record["selected_context_hash"]}]
        ),
        "bundle_file_hash": file_sha256(paths.sealed),
        "retrieval_trace_hash": file_sha256(paths.trace),
        "retrieval_summary_hash": file_sha256(paths.summary),
    }
    if retrieval_config is None:
        shared = _shared_values()
        retrieval_config = (
            {"values": shared, "hash": sha256(shared)}
            if minor == 0
            else retrieval_config_identity(shared, arm=arm)
        )
    meta = {
        "schema": schema,
        "artifact_type": "retrieval_bundle",
        "tag": tag,
        "holdout": holdout,
        "splits": ["holdout" if holdout else "regression"],
        "dataset_identity": dataset_identity
        or {"row_count": 1, "eval_ids": ["eval_001"], "ordered_row_hash": "rows"},
        "targets_identity": {"ordered_target_hash": "targets"},
        "corpus_identity": {"hash": corpus_hash},
        "index_identity": {"hash": "index"},
        "retrieval_config": retrieval_config,
        "generation_config": {"values": {}, "hash": sha256({})},
        **publication,
    }
    atomic_write_json(paths.meta, meta)
    atomic_write_json(paths.state, {"schema": schema, "state": "sealed", **publication})
    return paths


def test_original_only_1_1_pool_order_selection_and_hash_match_1_0(monkeypatch):
    from app.observability.context import TraceCollector, trace_context
    from app.retriever import hybrid_retriever as hybrid_module
    from app.retriever import reranker
    from app.retriever.context_selection import select_context

    dense_metadata = {"source_id": "s", "nested": {"kept": True}}
    dense = RetrievalResult("a", "A", 0.9, dense_metadata)
    sparse = RetrievalResult("a", "A", 4.0, {"source_id": "s"})

    class Model:
        def predict(self, pairs):
            return [3.0 for _ in pairs]

    monkeypatch.setattr(hybrid_module, "dense_retriever", lambda *_a, **_k: [dense])
    monkeypatch.setattr(hybrid_module, "sparse_retriever", lambda *_a, **_k: [sparse])
    monkeypatch.setattr(reranker, "_get_model", lambda: Model())
    knobs = RetrievalKnobs(
        dense_top_k=1,
        sparse_top_k=1,
        rerank_top_n=1,
        parent_expansion_enabled=False,
        prefer_operative_enabled=False,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=False,
        edge_expansion_enabled=False,
    )

    legacy_selection = select_context("q", knobs=knobs)
    collector = TraceCollector(capture_candidate_stages=True)
    with trace_context(trace_id="phase2", collector=collector):
        current_selection = select_context("q", knobs=knobs)

    assert [snapshot["stage"] for snapshot in collector.candidate_stages] == [
        "dense",
        "sparse",
        "fused",
        "fused",
        "reranked",
        "expanded",
        "selected",
    ]
    lane = collector.candidate_stages[2]
    canonical = collector.candidate_stages[3]
    assert (lane["query_variant"], canonical["query_variant"]) == (
        "original",
        "combined",
    )
    assert canonical["pool_role"] == "pre_rerank_pool"
    assert canonical["candidates"] == lane["candidates"]
    assert canonical["candidates"] is not lane["candidates"]
    assert canonical["candidates"][0]["metadata"] is not lane["candidates"][0]["metadata"]
    assert dense.metadata == dense_metadata

    legacy_stages = [deepcopy(lane)]
    assert _pre_rerank_pool_hash(
        legacy_stages, schema_minor=0
    ) == _pre_rerank_pool_hash(collector.candidate_stages, schema_minor=1)
    assert [result.chunk_id for result in current_selection.selected] == [
        result.chunk_id for result in legacy_selection.selected
    ]
    assert [result.text for result in current_selection.selected] == [
        result.text for result in legacy_selection.selected
    ]
    assert [result.metadata for result in current_selection.selected] == [
        result.metadata for result in legacy_selection.selected
    ]
    assert sha256(
        [
            {
                "chunk_id": result.chunk_id,
                "text": result.text,
                "score": result.score,
                "metadata": result.metadata,
            }
            for result in current_selection.selected
        ]
    ) == sha256(
        [
            {
                "chunk_id": result.chunk_id,
                "text": result.text,
                "score": result.score,
                "metadata": result.metadata,
            }
            for result in legacy_selection.selected
        ]
    )


def test_validation_accepts_1_0_and_rejects_unsupported_and_bad_1_1_pools(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    validate_sealed_bundle(_write_bundle("legacy", minor=0))
    with pytest.raises(ValueError, match="unsupported frozen-context schema minor"):
        validate_schema(schema_version(minor=2))

    missing = [
        stage
        for stage in _candidate_stages(1)
        if stage.get("pool_role") != "pre_rerank_pool"
    ]
    missing_paths = _write_bundle(
        "missing-pool",
        minor=1,
        candidate_stages=missing,
        compute_pool_hash=False,
    )
    with pytest.raises(ValueError, match="exactly one pool_role=pre_rerank_pool"):
        validate_sealed_bundle(missing_paths)

    duplicate = _candidate_stages(1)
    duplicate.insert(4, deepcopy(duplicate[3]))
    duplicate_paths = _write_bundle(
        "duplicate-pool",
        minor=1,
        candidate_stages=duplicate,
        compute_pool_hash=False,
    )
    with pytest.raises(ValueError, match="exactly one pool_role=pre_rerank_pool"):
        validate_sealed_bundle(duplicate_paths)


def test_lane_metrics_and_counts_do_not_double_count_canonical_fused_pool(tmp_path):
    hit = _candidate("hit", source="s")
    miss = _candidate("miss", source="other")
    stages = [
        {"stage": "dense", "query_variant": "original", "candidates": [hit]},
        {"stage": "dense", "query_variant": "legal_rewrite", "candidates": [miss]},
        {"stage": "sparse", "query_variant": "original", "candidates": [hit]},
        {"stage": "sparse", "query_variant": "legal_rewrite", "candidates": [miss]},
        {"stage": "fused", "query_variant": "original", "candidates": [hit]},
        {"stage": "fused", "query_variant": "legal_rewrite", "candidates": [miss]},
        {
            "stage": "fused",
            "query_variant": "combined",
            "pool_role": "pre_rerank_pool",
            "candidates": [hit, miss],
        },
    ]
    target = {
        "eval_id": "eval_001",
        "match_mode": "exact",
        "targets": [{"source_id": "s", "provision_id": "s:section:1"}],
    }
    trace_record = {"candidate_stages": stages, "retrieval_latency_ms": 5.0}
    lines = candidate_lines(
        {"id": "eval_001", "question": "q", "category": "factual"},
        {"abstained": False},
        trace_record,
        target,
    )
    candidate_count, stage_counts, variant_counts = candidate_count_metadata(stages)
    assert candidate_count == 6
    assert stage_counts == {"dense": 2, "sparse": 2, "fused": 2}
    assert variant_counts["fused"] == {"original": 1, "legal_rewrite": 1}

    trace_path = tmp_path / "trace.jsonl"
    append_completed_row(
        trace_path,
        "eval_001",
        lines,
        retrieval_latency_ms=5,
        abstained=False,
        category="factual",
        target_record=target,
        candidate_count=candidate_count,
        stage_candidate_counts=stage_counts,
        stage_candidate_counts_by_query_variant=variant_counts,
    )
    summary = build_retrieval_summary(trace_path)
    assert summary["operational"]["candidate_count_mean"] == 6.0
    assert summary["operational"]["stage_candidate_count_mean"]["fused"] == 2.0
    assert summary["overall"]["stages"]["fused"]["candidate_count_mean"] == 2.0
    lanes = summary["overall"]["stages_by_query_variant"]
    assert lanes["fused"]["original"]["candidate_count_mean"] == 1.0
    assert lanes["fused"]["legal_rewrite"]["candidate_count_mean"] == 1.0
    assert lanes["dense"]["original"]["provision_hit_at_k"]["1"] == 1.0
    assert lanes["dense"]["legal_rewrite"]["provision_hit_at_k"]["1"] == 0.0


def test_resume_rejects_full_retrieval_identity_mismatch(tmp_path, monkeypatch):
    from app.evals import retrieval_runner

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    row = {
        "id": "eval_001",
        "question": "question",
        "ground_truth": "truth",
        "expected_sources": ["s"],
        "category": "factual",
        "split": "regression",
    }
    dataset_identity = {
        "row_count": 1,
        "eval_ids": ["eval_001"],
        "ordered_row_hash": ordered_hash([sha256(row)]),
    }
    current, _ = retrieval_runner._config_identities(resolve_policy().policy)
    drifted = deepcopy(current)
    drifted["query_separation"]["timeout_seconds"] = 16.0
    drifted["full_hash"] = sha256(
        {
            "shared_hash": drifted["shared_hash"],
            "query_separation": drifted["query_separation"],
        }
    )
    _write_bundle(
        "resume-mismatch",
        minor=1,
        retrieval_config=drifted,
        dataset_identity=dataset_identity,
    )
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            "eval_001": {
                "eval_id": "eval_001",
                "match_mode": "exact",
                "targets": [{"source_id": "s", "provision_id": "s:section:1"}],
            }
        },
    )

    with pytest.raises(ValueError, match="full identity"):
        retrieval_runner.retrieve_rows([row], tag="resume-mismatch", resume=True)


def test_resume_rejects_query_separation_arm_mismatch_before_rewrite(
    tmp_path, monkeypatch
):
    from app.evals import retrieval_runner

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path / "results"))
    monkeypatch.setattr(
        settings,
        "legal_query_rewrite_cache_dir",
        str(tmp_path / "rewrite-cache"),
    )
    row = {
        "id": "eval_001",
        "question": "question",
        "ground_truth": "truth",
        "expected_sources": ["s"],
        "category": "factual",
        "split": "regression",
    }
    dataset_identity = {
        "row_count": 1,
        "eval_ids": ["eval_001"],
        "ordered_row_hash": ordered_hash([sha256(row)]),
    }
    _write_bundle(
        "resume-arm-mismatch",
        minor=1,
        arm="original_only",
        dataset_identity=dataset_identity,
    )
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            "eval_001": {
                "eval_id": "eval_001",
                "match_mode": "exact",
                "targets": [{"source_id": "s", "provision_id": "s:section:1"}],
            }
        },
    )
    monkeypatch.setattr(
        "app.retriever.legal_query_rewriter._call_haiku",
        lambda _prompt: pytest.fail("model call attempted during resume rejection"),
    )
    with pytest.raises(ValueError, match="full identity"):
        retrieval_runner.retrieve_rows(
            [row],
            tag="resume-arm-mismatch",
            resume=True,
            query_separation_arm="original_plus_rewrite",
        )


def test_comparator_accepts_legacy_baseline_and_reports_row_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("baseline", minor=0)
    _write_bundle(
        "candidate",
        minor=1,
        arm="original_plus_rewrite",
        pool_text="changed pool",
        selected_text="changed context",
    )

    report_path = compare_retrieval_bundles(
        "baseline", "candidate", tag="comparison"
    )
    report = json.loads(report_path.read_text())
    assert report["baseline_arm"] == "original_only"
    assert report["candidate_arm"] == "original_plus_rewrite"
    assert report["summary"] == {
        "row_count": 1,
        "pre_rerank_pool_changed": 1,
        "selected_context_changed": 1,
    }
    assert report["rows"][0]["pre_rerank_pool_changed"] is True
    meta = json.loads((report_path.parent / "meta.json").read_text())
    assert meta["artifact_type"] == "retrieval_comparison"
    assert meta["report_hash"] == file_sha256(report_path)


def test_comparator_accepts_declared_sibling_delta_and_profile_label_difference(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("sibling-baseline", minor=1)
    candidate_shared = _shared_values()
    candidate_shared["profile"] = "local"
    candidate_shared["retrieval_defaults"].update(
        {
            "sibling_expansion_enabled": True,
            "sibling_expansion_radius": 1,
            "sibling_expansion_max_chars": 3000,
            "sibling_expansion_max_tokens": 750,
        }
    )
    _write_bundle(
        "sibling-candidate",
        minor=1,
        retrieval_config=retrieval_config_identity(
            candidate_shared, arm="original_only"
        ),
        selected_text="selected law with sibling",
    )

    report_path = compare_retrieval_bundles(
        "sibling-baseline",
        "sibling-candidate",
        tag="sibling-comparison",
        expected_arm_pair=("original_only", "original_only"),
        expected_knob_diff={"sibling_expansion_enabled": (False, True)},
    )
    report = json.loads(report_path.read_text())

    expected_delta = {
        "sibling_expansion_enabled": {"baseline": False, "candidate": True}
    }
    assert report["expected_arm_pair"] == {
        "baseline": "original_only",
        "candidate": "original_only",
    }
    assert report["declared_knob_diff"] == expected_delta
    assert report["observed_knob_diff"] == expected_delta
    assert report["baseline_raw_shared_hash"] != report["candidate_raw_shared_hash"]
    assert report["shared_hash"] == report["baseline_raw_shared_hash"]
    assert report["shared_hash_alias"] == "baseline_raw_shared_hash"
    assert report["comparable_shared_hash"]
    assert report["profile_labels"] == {
        "baseline": "eval",
        "candidate": "local",
        "matched": False,
        "severity": "informational",
        "affects_pass_fail": False,
    }
    assert report["summary"]["selected_context_changed"] == 1


def test_comparator_rejects_undeclared_wrong_unobserved_and_unknown_knob_deltas(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("delta-baseline", minor=1)
    candidate_shared = _shared_values()
    candidate_shared["retrieval_defaults"]["sibling_expansion_enabled"] = True
    _write_bundle(
        "delta-candidate",
        minor=1,
        retrieval_config=retrieval_config_identity(
            candidate_shared, arm="original_only"
        ),
    )

    kwargs = {
        "baseline_tag": "delta-baseline",
        "candidate_tag": "delta-candidate",
        "expected_arm_pair": ("original_only", "original_only"),
    }
    with pytest.raises(ValueError, match="undeclared=sibling_expansion_enabled"):
        compare_retrieval_bundles(**kwargs, tag="undeclared")
    with pytest.raises(ValueError, match="wrong_endpoints=sibling_expansion_enabled"):
        compare_retrieval_bundles(
            **kwargs,
            tag="wrong-endpoints",
            expected_knob_diff={"sibling_expansion_enabled": (True, False)},
        )
    with pytest.raises(ValueError, match="unobserved=sibling_expansion_radius"):
        compare_retrieval_bundles(
            **kwargs,
            tag="unobserved",
            expected_knob_diff={
                "sibling_expansion_enabled": (False, True),
                "sibling_expansion_radius": (1, 2),
            },
        )
    with pytest.raises(ValueError, match="unknown retrieval selection knob"):
        compare_retrieval_bundles(
            **kwargs,
            tag="unknown",
            expected_knob_diff={"not_a_knob": (False, True)},
        )


def test_comparator_rejects_shared_query_and_corpus_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("baseline", minor=0)

    changed_shared = _shared_values()
    changed_shared["retrieval_defaults"]["dense_top_k"] = 31
    _write_bundle(
        "shared-drift",
        minor=1,
        retrieval_config=retrieval_config_identity(
            changed_shared, arm="original_plus_rewrite"
        ),
    )
    with pytest.raises(ValueError, match="shared values mismatch"):
        compare_retrieval_bundles("baseline", "shared-drift", tag="no-shared")

    query_drift = retrieval_config_identity(
        _shared_values(), arm="original_plus_rewrite"
    )
    query_drift["query_separation"]["max_tokens"] = 161
    query_drift["full_hash"] = sha256(
        {
            "shared_hash": query_drift["shared_hash"],
            "query_separation": query_drift["query_separation"],
        }
    )
    _write_bundle("query-drift", minor=1, retrieval_config=query_drift)
    with pytest.raises(ValueError, match="query-separation config mismatch"):
        compare_retrieval_bundles("baseline", "query-drift", tag="no-query")

    _write_bundle(
        "corpus-drift",
        minor=1,
        arm="original_plus_rewrite",
        corpus_hash="different",
    )
    with pytest.raises(ValueError, match="identity mismatch: corpus"):
        compare_retrieval_bundles("baseline", "corpus-drift", tag="no-corpus")


def test_comparator_rejects_holdout_before_report_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("baseline-holdout", minor=0, holdout=True)
    _write_bundle("candidate", minor=1, arm="original_plus_rewrite")

    with pytest.raises(ValueError, match="holdout"):
        compare_retrieval_bundles(
            "baseline-holdout", "candidate", tag="forbidden-report"
        )
    assert not list(tmp_path.rglob("forbidden-report"))


def test_comparator_rejects_row_level_holdout_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    baseline = _write_bundle("baseline-row-holdout", minor=0)
    rows = read_hashed_rows(baseline.sealed)
    row = {key: value for key, value in rows[0].items() if key != "record_hash"}
    row["split"] = "holdout"
    baseline.sealed.unlink()
    append_hashed_row(baseline.sealed, row)
    rewritten = read_hashed_rows(baseline.sealed)[0]
    publication = {
        "row_count": 1,
        "ordered_record_hash": ordered_hash([rewritten["record_hash"]]),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [
                {
                    "eval_id": rewritten["eval_id"],
                    "hash": rewritten["pre_rerank_pool_hash"],
                }
            ]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [
                {
                    "eval_id": rewritten["eval_id"],
                    "hash": rewritten["selected_context_hash"],
                }
            ]
        ),
        "bundle_file_hash": file_sha256(baseline.sealed),
        "retrieval_trace_hash": file_sha256(baseline.trace),
        "retrieval_summary_hash": file_sha256(baseline.summary),
    }
    meta = json.loads(baseline.meta.read_text())
    meta.update(publication)
    atomic_write_json(baseline.meta, meta)
    state = json.loads(baseline.state.read_text())
    state.update(publication)
    atomic_write_json(baseline.state, state)
    _write_bundle("candidate-row-check", minor=1, arm="original_plus_rewrite")

    with pytest.raises(ValueError, match="holdout"):
        compare_retrieval_bundles(
            "baseline-row-holdout",
            "candidate-row-check",
            tag="forbidden-row-report",
        )
    assert not list(tmp_path.rglob("forbidden-row-report"))


def test_comparator_rejects_existing_tag_from_another_date(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _write_bundle("baseline", minor=0)
    _write_bundle("candidate", minor=1, arm="original_plus_rewrite")
    historical = tmp_path / "runs" / "2000-01-01" / "global-tag"
    historical.mkdir(parents=True)

    with pytest.raises(FileExistsError, match="tag already exists"):
        compare_retrieval_bundles("baseline", "candidate", tag="global-tag")
    assert list(tmp_path.rglob("global-tag")) == [historical]


def test_comparator_cli_rejects_unknown_knob(monkeypatch):
    monkeypatch.setattr("app.cli.main.configure_logging", lambda: None)
    result = CliRunner().invoke(
        app,
        [
            "eval-retrieval-compare",
            "baseline",
            "candidate",
            "--tag",
            "unknown",
            "--expected-knob-diff",
            "not_a_knob=[false,true]",
        ],
    )
    assert result.exit_code != 0
    assert "unknown retrieval selection knob" in result.output


def test_comparator_cli_and_import_isolation(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    captured = {}

    def fake_compare(baseline, candidate, **kwargs):
        captured.update(
            {"baseline": baseline, "candidate": candidate, **kwargs}
        )
        return output

    monkeypatch.setattr(
        "app.evals.retrieval_comparison.compare_retrieval_bundles",
        fake_compare,
    )
    monkeypatch.setattr("app.cli.main.configure_logging", lambda: None)
    result = CliRunner().invoke(
        app,
        [
            "eval-retrieval-compare",
            "baseline",
            "candidate",
            "--tag",
            "report",
            "--expected-baseline-arm",
            "original_only",
            "--expected-candidate-arm",
            "original_only",
            "--expected-knob-diff",
            "sibling_expansion_enabled=[false,true]",
        ],
    )
    assert result.exit_code == 0, result.output
    assert str(output) in result.output
    assert captured == {
        "baseline": "baseline",
        "candidate": "candidate",
        "tag": "report",
        "expected_arm_pair": ("original_only", "original_only"),
        "expected_knob_diff": {"sibling_expansion_enabled": (False, True)},
    }

    code = (
        "import sys; import app.evals.retrieval_comparison; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    probe = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert probe.returncode == 0, probe.stderr
    loaded = set(probe.stdout.splitlines())
    forbidden = (
        "anthropic",
        "torch",
        "sentence_transformers",
        "app.evals.generation_replay",
        "app.pipeline.runner",
        "app.pipeline.frozen_generation",
        "app.retriever",
        "app.indexing.embedder",
        "qdrant_client",
    )
    for module in forbidden:
        assert not any(name == module or name.startswith(module + ".") for name in loaded)
