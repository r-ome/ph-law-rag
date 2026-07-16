import json

import pytest

from app.evals.retrieval_metrics import build_retrieval_summary
from app.evals.retrieval_targets import (
    load_retrieval_targets,
    target_match_flags,
    validate_retrieval_targets,
)
from app.evals.retrieval_trace import (
    append_completed_row,
    candidate_lines,
    read_completed_trace,
)
from app.evals.report import build_summary
from app.pipeline.runner import run_answer
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _dataset_row(eval_id="eval_001", **overrides):
    row = {
        "id": eval_id,
        "split": "regression",
        "category": "factual",
    }
    row.update(overrides)
    return row


def _target(eval_id="eval_001", **overrides):
    record = {
        "eval_id": eval_id,
        "match_mode": "exact",
        "targets": [
            {
                "source_id": "source_a",
                "provision_id": "source_a:section:1",
                "unit_label": "Section 1(a)",
            }
        ],
    }
    record.update(overrides)
    return record


def test_target_validation_covers_exact_source_only_multi_target_and_holdout():
    dataset = [
        _dataset_row(),
        _dataset_row("eval_002", category="out-of-scope"),
        _dataset_row("eval_003", split="holdout"),
    ]
    records = [
        _target(),
        _target("eval_002", targets=[]),
    ]
    validated = validate_retrieval_targets(records, dataset, {"source_a"})
    assert set(validated) == {"eval_001", "eval_002"}
    assert target_match_flags(
        source_id="source_a",
        provision_id="source_a:section:1",
        unit_label="Section 1(a)",
        target_record=validated["eval_001"],
    ) == {
        "expected_source_match": True,
        "expected_provision_match": True,
        "expected_leaf_match": True,
    }

    source_only = _target(
        match_mode="source_only",
        targets=[{"source_id": "source_a", "provision_id": None}],
    )
    source_only_flags = target_match_flags(
        source_id="source_a",
        provision_id="wrong",
        unit_label="",
        target_record=source_only,
    )
    assert source_only_flags["expected_source_match"] is True
    assert source_only_flags["expected_provision_match"] is None

    with pytest.raises(ValueError, match="duplicate eval id"):
        validate_retrieval_targets([records[0], records[0], records[1]], dataset, {"source_a"})
    with pytest.raises(ValueError, match="missing retrieval target eval IDs"):
        validate_retrieval_targets([records[0]], dataset, {"source_a"})
    with pytest.raises(ValueError, match="unknown source_id"):
        validate_retrieval_targets(
            [_target(targets=[{"source_id": "missing", "provision_id": "p"}]), records[1]],
            dataset,
            {"source_a"},
        )
    with pytest.raises(ValueError, match="holdout target is prohibited"):
        validate_retrieval_targets([*records, _target("eval_003")], dataset, {"source_a"})


def test_tracked_retrieval_targets_cover_non_holdout_without_holdout_labels():
    targets = load_retrieval_targets()

    assert len(targets) == 131
    assert set(targets).isdisjoint({f"eval_{number:03d}" for number in range(132, 162)})
    assert {
        eval_id
        for eval_id, record in targets.items()
        if record["match_mode"] == "source_only"
    } == {"eval_026", "eval_070", "eval_131"}
    assert targets["eval_124"]["targets"][1:3] == [
        {"source_id": "ip_code", "provision_id": "ip_code:chapter-xiii:section:145"},
        {"source_id": "ip_code", "provision_id": "ip_code:chapter-xiii:section:146"},
    ]


def test_candidate_trace_is_crash_tolerant_and_metrics_use_completed_rows(tmp_path):
    trace_record = {
        "retrieval_latency_ms": 25.0,
        "candidate_stages": [
            {
                "stage": "dense",
                "query_variant": "original",
                "query_text": "question",
                "query_ordinal": 0,
                "candidates": [
                    {
                        "rank": 1,
                        "chunk_id": "hit",
                        "text": "abcdefgh",
                        "score": 0.9,
                        "dense_score": 0.9,
                        "metadata": {
                            "source_id": "source_a",
                            "provision_id": "source_a:section:1",
                            "unit_label": "Section 1(a)",
                        },
                    }
                ],
            },
            {
                "stage": "sparse",
                "query_variant": "original",
                "query_text": "question",
                "query_ordinal": 0,
                "candidates": [],
            },
            {
                "stage": "fused",
                "query_variant": "original",
                "query_text": "question",
                "query_ordinal": 0,
                "candidates": [
                    {
                        "rank": 1,
                        "chunk_id": "hit",
                        "text": "abcdefgh",
                        "score": 0.03,
                        "fused_score": 0.03,
                        "metadata": {
                            "source_id": "source_a",
                            "provision_id": "source_a:section:1",
                            "unit_label": "Section 1(a)",
                        },
                    }
                ],
            },
            {
                "stage": "reranked",
                "query_variant": "original",
                "query_text": "question",
                "query_ordinal": 0,
                "candidates": [
                    {
                        "rank": 1,
                        "chunk_id": "hit",
                        "text": "abcdefgh",
                        "score": 4.0,
                        "rerank_score": 4.0,
                        "survived": True,
                        "metadata": {
                            "source_id": "source_a",
                            "provision_id": "source_a:section:1",
                            "unit_label": "Section 1(a)",
                        },
                    }
                ],
            },
            {
                "stage": "selected",
                "query_variant": "original",
                "query_text": "question",
                "query_ordinal": 0,
                "candidates": [
                    {
                        "rank": 1,
                        "chunk_id": "hit",
                        "text": "abcdefgh",
                        "score": 4.0,
                        "selected": True,
                        "metadata": {
                            "source_id": "source_a",
                            "provision_id": "source_a:section:1",
                            "unit_label": "Section 1(a)",
                        },
                    }
                ],
            },
        ],
    }
    item = {
        "id": "eval_001",
        "category": "factual",
        "split": "regression",
        "question": "question",
    }
    target = _target()
    lines = candidate_lines(item, {"abstained": False}, trace_record, target)
    path = tmp_path / "retrieval_trace.jsonl"
    append_completed_row(
        path,
        "eval_001",
        lines,
        retrieval_latency_ms=25,
        abstained=False,
        category="factual",
        target_record=target,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{malformed\n")
        handle.write(json.dumps({**lines[0], "eval_id": "eval_002"}) + "\n")

    completed = read_completed_trace(path)
    assert {line["eval_id"] for line in completed} == {"eval_001"}
    assert lines[0]["token_estimate"] == 2
    summary = build_retrieval_summary(path)
    assert summary["overall"]["stages"]["dense"]["provision_hit_at_k"]["1"] == 1.0
    assert summary["overall"]["stages"]["selected"]["leaf_mrr"] == 1.0
    assert summary["overall"]["target_survival"]["selected"] == 1.0
    assert summary["overall"]["final_context"]["characters_mean"] == 8.0
    assert summary["overall"]["retrieval_latency_ms_mean"] == 25.0


def test_holdout_summary_contains_operational_metrics_only(tmp_path):
    path = tmp_path / "retrieval_trace.jsonl"
    append_completed_row(
        path,
        "eval_132",
        [],
        retrieval_latency_ms=11,
        abstained=False,
        category=None,
        candidate_count=7,
    )
    summary = build_retrieval_summary(path, holdout=True)
    assert summary == {
        "available": True,
        "holdout": True,
        "operational": {
            "rows": 1,
            "candidate_count_mean": 7.0,
            "retrieval_latency_ms_mean": 11.0,
        },
    }
    assert "by_category" not in summary
    assert "overall" not in summary


def test_summary_attributes_sibling_leaf_recovery_before_and_after_dedup(tmp_path):
    path = tmp_path / "retrieval_trace.jsonl"
    records = [
        {
            "record_type": "candidate",
            "eval_id": "eval_sibling",
            "stage": "reranked",
            "snapshot_ordinal": 1,
            "rank": 1,
            "chunk_id": "seed",
            "survived": True,
            "matched_leaf_targets": [],
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_sibling",
            "stage": "expanded",
            "snapshot_ordinal": 2,
            "rank": 2,
            "chunk_id": "target",
            "text": "target leaf",
            "expanded_from_sibling": True,
            "expected_leaf_match": True,
            "matched_leaf_targets": ["law|law:section:1|Section 1(b)"],
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_sibling",
            "stage": "selected",
            "snapshot_ordinal": 3,
            "rank": 2,
            "chunk_id": "target",
            "text": "target leaf",
            "matched_leaf_targets": ["law|law:section:1|Section 1(b)"],
        },
        {
            "record_type": "row_complete",
            "eval_id": "eval_sibling",
            "target_source_count": 1,
            "target_provision_count": 1,
            "target_leaf_count": 1,
            "stage_candidate_counts": {"reranked": 1, "expanded": 1, "selected": 1},
            "stage_timings_ms": {"sibling_expansion": 0.5},
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))

    sibling = build_retrieval_summary(path)["overall"]["sibling_expansion"]

    assert sibling["rows_fired"] == 1
    assert sibling["chunks_added_total"] == 1
    assert sibling["target_bearing_additions"] == 1
    assert sibling["leaf_rows_missed_after_rerank"] == 1
    assert sibling["leaf_rows_recovered_at_expanded"] == 1
    assert sibling["leaf_rows_recovered_at_selected"] == 1
    assert sibling["missed_leaf_recovery_rate"] == 1.0


def test_candidate_capture_forces_internal_trace_when_debug_and_logging_are_off(monkeypatch):
    from app.pipeline import runner

    monkeypatch.setattr(runner.settings, "debug", False)
    monkeypatch.setattr(runner.settings, "trace_logging_enabled", False)
    monkeypatch.setattr(runner, "is_conversational", lambda _question: True)

    response, trace = run_answer(
        "hello",
        debug=False,
        trace=False,
        capture_candidate_stages=True,
    )

    assert response["answer"]
    assert trace is not None
    assert trace["candidate_stages"] == []


def test_candidate_snapshots_are_not_written_to_operational_trace_api(monkeypatch):
    from app.pipeline import runner

    written = []
    monkeypatch.setattr(runner.settings, "trace_logging_enabled", True)
    monkeypatch.setattr(runner, "is_conversational", lambda _question: True)
    monkeypatch.setattr(runner.TraceWriter, "write", lambda _self, record: written.append(record))

    _, internal_trace = run_answer(
        "hello",
        debug=False,
        trace=True,
        capture_candidate_stages=True,
    )

    assert internal_trace is not None and "candidate_stages" in internal_trace
    assert len(written) == 1
    assert "candidate_stages" not in written[0]


def test_capture_preserves_context_order_and_emits_all_baseline_stages(monkeypatch):
    from app.observability.context import TraceCollector, trace_context
    from app.retriever import hybrid_retriever as hybrid_module
    from app.retriever import reranker
    from app.retriever.context_selection import select_context

    class FakeModel:
        def predict(self, pairs):
            return [3.0 if text == "A" else 2.0 for _, text in pairs]

    monkeypatch.setattr(
        hybrid_module,
        "dense_retriever",
        lambda _query, knobs=None: [
            RetrievalResult("a", "A", 0.9, {"source_id": "s", "provision_id": "p1"}),
            RetrievalResult("b", "B", 0.8, {"source_id": "s", "provision_id": "p2"}),
        ],
    )
    monkeypatch.setattr(
        hybrid_module,
        "sparse_retriever",
        lambda _query, knobs=None: [
            RetrievalResult("b", "B", 4.0, {"source_id": "s", "provision_id": "p2"})
        ],
    )
    monkeypatch.setattr(reranker, "_get_model", lambda: FakeModel())
    knobs = RetrievalKnobs(
        dense_top_k=2,
        sparse_top_k=1,
        rerank_top_n=2,
        rerank_score_margin=6.0,
        parent_expansion_enabled=False,
        prefer_operative_enabled=False,
        retrieval_operative_only=False,
        consolidated_dedup_enabled=False,
        edge_expansion_enabled=False,
    )

    without_capture = select_context("q", knobs=knobs)
    collector = TraceCollector(capture_candidate_stages=True)
    with trace_context(trace_id="trace", collector=collector):
        with_capture = select_context("q", knobs=knobs)

    assert [r.chunk_id for r in with_capture.selected] == [
        r.chunk_id for r in without_capture.selected
    ]
    assert [r.score for r in with_capture.selected] == [
        r.score for r in without_capture.selected
    ]
    assert [snapshot["stage"] for snapshot in collector.candidate_stages] == [
        "dense",
        "sparse",
        "fused",
        "fused",
        "reranked",
        "expanded",
        "selected",
    ]
    assert collector.candidate_stages[2]["query_variant"] == "original"
    assert collector.candidate_stages[3]["query_variant"] == "combined"
    assert collector.candidate_stages[3]["pool_role"] == "pre_rerank_pool"


def test_eval_summary_keeps_all_row_abstention_counts_and_ragas_n():
    import pandas as pd

    class RagasResult:
        def to_pandas(self):
            return pd.DataFrame([{"faithfulness": 0.8}])

    answered = {
        "eval_id": "eval_001",
        "category": "factual",
        "abstained": False,
        "retrieval_target_present": True,
    }
    false_abstention = {
        "eval_id": "eval_002",
        "category": "ambiguous",
        "abstained": True,
        "retrieval_target_present": True,
    }
    expected_abstention = {
        "eval_id": "eval_003",
        "category": "out-of-scope",
        "abstained": True,
        "retrieval_target_present": False,
    }
    results = [answered, false_abstention, expected_abstention]

    summary = build_summary(results, (RagasResult(), [answered]))

    assert summary["overall"]["n"] == 1
    assert summary["overall"]["all_rows"] == 3
    assert summary["abstention"]["expected_abstentions"] == 1
    assert summary["abstention"]["correct_abstentions"] == 1
    assert summary["abstention"]["false_abstentions"] == 1
    assert summary["abstention"]["answer_leaks"] == 0
    assert summary["abstention"]["target_present_despite_abstention"] == 1
    assert summary["by_category"]["ambiguous"]["n"] == 0
    assert summary["by_category"]["ambiguous"]["all_rows"] == 1
