import json
from datetime import datetime

from app.evals import artifacts
from app.evals.report import save_scored


class FakeRagasResult:
    def to_pandas(self):
        import pandas as pd

        return pd.DataFrame([
            {
                "faithfulness": 1.0,
                "answer_relevancy": 0.75,
                "llm_context_precision_with_reference": 0.5,
                "context_recall": 0.25,
            }
        ])


def test_save_scored_updates_bundled_meta_manifest_and_latest(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))

    tag = "model_label_20260706_103554"
    paths = artifacts.create_run_paths(tag, datetime(2026, 7, 6, 10, 35, 54))
    result = {
        "question": "question",
        "answer": "answer",
        "contexts": ["context"],
        "ground_truth": "reference",
        "category": "factual",
        "abstained": False,
    }
    paths.run.write_text(json.dumps(result) + "\n", encoding="utf-8")
    artifacts.save_meta(tag, {
        "tag": tag,
        "date": "2026-07-06",
        "model": "model",
        "label": "label",
        "question_count": 1,
        "scored_count": None,
    })

    save_scored([result], (FakeRagasResult(), [result]), run_tag=tag)

    assert paths.summary.exists()
    assert paths.scored.exists()
    assert not (results_dir / "scored_latest.json").exists()
    assert artifacts.read_json(paths.meta)["scored_count"] == 1

    manifest_rows = [
        json.loads(line)
        for line in (results_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest_rows == [
        {
            "abstention_accuracy": 1.0,
            "answer_relevancy": 0.75,
            "context_precision": 0.5,
            "context_recall": 0.25,
            "date": "2026-07-06",
            "faithfulness": 1.0,
            "git_sha": None,
            "holdout": False,
            "label": "label",
            "layout": "bundled",
            "model": "model",
            "questions": 1,
            "run_path": f"runs/2026-07-06/{tag}/run.jsonl",
            "scored": 1,
            "scored_path": f"runs/2026-07-06/{tag}/scored.json",
            "summary_path": f"runs/2026-07-06/{tag}/summary.json",
            "tag": tag,
        }
    ]
    assert artifacts.read_json(results_dir / "latest.json")["tag"] == tag
