import json

import pytest

from app.evals import runner

pytestmark = pytest.mark.unit


def test_run_rows_persists_selected_chunk_ids_and_strategy_override(tmp_path, monkeypatch):
    captured = {}

    def fake_run_answer(
        question,
        debug=None,
        trace_label=None,
        strategy_override=None,
        capture_candidate_stages=False,
    ):
        captured["debug"] = debug
        captured["trace_label"] = trace_label
        captured["strategy_override"] = strategy_override
        captured["capture_candidate_stages"] = capture_candidate_stages
        response = {
            "answer": "answer",
            "contexts": ["ctx"],
            "sources": [{"source_id": "source_a"}],
            "context_sources": ["source_a"],
            "abstained": False,
            "corrective_retrieval": {
                "enabled": True,
                "fired": True,
                "added_chunks": 1,
                "baseline_selected_count": 2,
                "post_selected_count": 3,
                "max_added": 2,
            },
            "debug": {
                "chunks": [
                    {"chunk_id": "chunk-1"},
                    {"chunk_id": "chunk-2"},
                ],
                "stages": [{"name": "prefer_operative", "fired": True}],
            },
        }
        trace = {
            "retrieval_latency_ms": 12.5,
            "candidate_stages": [
                {
                    "stage": "selected",
                    "query_variant": "original",
                    "query_text": question,
                    "query_ordinal": 0,
                    "candidates": [],
                }
            ],
        }
        return response, trace

    monkeypatch.setattr(runner, "run_answer", fake_run_answer)
    out_path = tmp_path / "run.jsonl"

    rows = runner.run_rows(
        [
            {
                "id": "eval_001",
                "split": "regression",
                "question": "Question?",
                "ground_truth": "Truth",
                "category": "factual",
                "expected_sources": ["source_a"],
                "topic": "topic",
                "facet": "lookup",
            }
        ],
        out_path,
        strategy_override="current_law",
        trace_label="r5",
    )

    assert captured == {
        "debug": True,
        "trace_label": "r5",
        "strategy_override": "current_law",
        "capture_candidate_stages": True,
    }
    assert rows[0]["selected_chunk_ids"] == ["chunk-1", "chunk-2"]
    assert rows[0]["corrective_retrieval"] == {
        "enabled": True,
        "fired": True,
        "added_chunks": 1,
        "baseline_selected_count": 2,
        "post_selected_count": 3,
        "max_added": 2,
    }
    assert rows[0]["debug_stages"] == [{"name": "prefer_operative", "fired": True}]
    assert rows[0]["eval_id"] == "eval_001"
    assert rows[0]["split"] == "regression"
    assert rows[0]["facet"] == "lookup"
    assert rows[0]["topic"] == "topic"
    written = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    assert written == rows
    assert (tmp_path / "retrieval_trace.jsonl").exists()
    assert (tmp_path / "retrieval_summary.json").exists()


def test_holdout_run_writes_only_aggregate_retrieval_summary(tmp_path, monkeypatch):
    response = {
        "answer": "answer",
        "contexts": ["context"],
        "sources": [],
        "context_sources": ["secret_source"],
        "abstained": False,
        "debug": {"chunks": [], "stages": []},
    }
    trace = {
        "retrieval_latency_ms": 9.0,
        "retrieval_stage_timings_ms": {"dense": 4.0},
        "candidate_stages": [
            {
                "stage": "dense",
                "query_variant": "original",
                "query_text": "secret question",
                "query_ordinal": 0,
                "candidates": [
                    {
                        "chunk_id": "secret-chunk",
                        "text": "secret text",
                        "score": 1.0,
                        "metadata": {"source_id": "secret_source"},
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(runner, "run_answer", lambda *args, **kwargs: (response, trace))

    runner.run_rows(
        [
            {
                "id": "eval_132",
                "split": "holdout",
                "question": "secret question",
                "ground_truth": "secret truth",
                "category": "factual",
            }
        ],
        tmp_path / "run.jsonl",
        holdout=True,
    )

    assert not (tmp_path / "retrieval_trace.jsonl").exists()
    summary = json.loads((tmp_path / "retrieval_summary.json").read_text())
    assert summary == {
        "available": True,
        "holdout": True,
        "operational": {
            "rows": 1,
            "candidate_count_mean": 1,
            "retrieval_latency_ms_mean": 9.0,
        },
    }
