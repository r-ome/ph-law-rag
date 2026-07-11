import json
import math

import pytest

from app.evals import repeatability


class FakeRagasResult:
    def __init__(self, rows):
        self.rows = rows

    def to_pandas(self):
        import pandas as pd

        return pd.DataFrame(self.rows)


def test_repeatability_panel_bypasses_cache_and_writes_artifact(tmp_path, monkeypatch):
    run_path = tmp_path / "run.jsonl"
    rows = [
        {"eval_id": "eval_001", "question": "q1", "answer": "a", "contexts": ["ctx"], "ground_truth": "g", "abstained": False},
        {"eval_id": "eval_002", "question": "q2", "answer": "a", "contexts": ["ctx"], "ground_truth": "g", "abstained": False},
    ]
    run_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    calls = []

    def fake_score(panel, use_cache=True):
        calls.append(use_cache)
        value = 0.1 * len(calls)
        return FakeRagasResult([
            {"faithfulness": value, "answer_relevancy": 1.0},
            {"faithfulness": value + 0.2, "answer_relevancy": 0.5},
        ]), panel

    monkeypatch.setattr("app.evals.ragas_scorer.score", fake_score)
    monkeypatch.setattr("app.evals.ragas_scorer.judge_model", lambda: "judge")
    monkeypatch.setattr(repeatability.artifacts.settings, "eval_results_dir", str(tmp_path / "eval_results"))

    payload, out_path = repeatability.run_repeatability_panel(run_path, repeats=3)

    assert calls == [False, False, False]
    assert out_path.exists()
    assert payload["cache"] == "bypassed"
    assert payload["row_count"] == 2
    assert payload["run_sha256"]
    assert payload["panel_sha256"]
    assert "ragas_version" in payload
    assert "git_sha" in payload
    assert payload["scorer_identity"]["metrics"] == [
        "Faithfulness",
        "ResponseRelevancy",
        "LLMContextPrecisionWithReference",
        "LLMContextRecall",
    ]
    assert payload["metrics"]["faithfulness"]["max_within_row_range"] == 0.2
    assert payload["caveat"] == repeatability.CAVEAT


def test_repeatability_rejects_holdout_artifacts(tmp_path, monkeypatch):
    run_path = tmp_path / "run.jsonl"
    run_path.write_text(
        json.dumps({
            "eval_id": "eval_140",
            "split": "holdout",
            "question": "private",
            "answer": "a",
            "contexts": ["ctx"],
            "ground_truth": "private",
            "abstained": False,
        }) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repeatability.artifacts.settings, "eval_results_dir", str(tmp_path / "eval_results"))

    with pytest.raises(ValueError, match="cannot use holdout"):
        repeatability.run_repeatability_panel(run_path, repeats=2)


def test_repeatability_row_id_selection_nan_handling_and_percentile(tmp_path, monkeypatch):
    run_path = tmp_path / "run.jsonl"
    rows = [
        {"eval_id": "eval_001", "question": "q1", "answer": "a", "contexts": ["ctx"], "ground_truth": "g", "abstained": False},
        {"eval_id": "eval_002", "question": "q2", "answer": "a", "contexts": ["ctx"], "ground_truth": "g", "abstained": False},
    ]
    run_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    frames = [
        [{"faithfulness": 0.2, "answer_relevancy": math.nan}],
        [{"faithfulness": 0.5, "answer_relevancy": 0.9}],
    ]
    seen_panels = []

    def fake_score(panel, use_cache=True):
        seen_panels.append(panel)
        return FakeRagasResult(frames[len(seen_panels) - 1]), panel

    monkeypatch.setattr("app.evals.ragas_scorer.score", fake_score)
    monkeypatch.setattr("app.evals.ragas_scorer.judge_model", lambda: "judge")
    monkeypatch.setattr(repeatability.artifacts.settings, "eval_results_dir", str(tmp_path / "eval_results"))

    payload, _ = repeatability.run_repeatability_panel(run_path, repeats=2, row_ids=["eval_002"])

    assert all([row["eval_id"] for row in panel] == ["eval_002"] for panel in seen_panels)
    assert payload["rows"] == ["eval_002"]
    assert payload["metrics"]["faithfulness"]["max_within_row_range"] == 0.3
    assert payload["metrics"]["answer_relevancy"]["nan_count"] == 1
    assert payload["metrics"]["answer_relevancy"]["rows_with_range"] == 0
    assert repeatability._percentile([0.0, 10.0, 20.0], 0.9) == 18.0
