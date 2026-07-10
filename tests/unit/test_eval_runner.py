import json

import pytest

from app.evals import runner

pytestmark = pytest.mark.unit


def test_run_rows_persists_selected_chunk_ids_and_strategy_override(tmp_path, monkeypatch):
    captured = {}

    def fake_answer(question, debug=None, trace_label=None, strategy_override=None):
        captured["debug"] = debug
        captured["trace_label"] = trace_label
        captured["strategy_override"] = strategy_override
        return {
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

    monkeypatch.setattr(runner, "answer", fake_answer)
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
