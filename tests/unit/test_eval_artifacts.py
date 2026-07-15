import json
from datetime import datetime

from app.evals import artifacts
from app.evals.retrieval_metrics import rebuild_for_tag
from app.evals.retrieval_trace import append_completed_row
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


def test_retrieval_artifact_paths_support_bundled_and_legacy_layouts(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))

    tag = "bundled_20260714_120000"
    bundled = artifacts.create_run_paths(tag, datetime(2026, 7, 14, 12, 0, 0))
    bundled.retrieval_trace.write_text("", encoding="utf-8")
    artifacts.write_json(bundled.retrieval_summary, {"available": True})
    artifacts.write_latest(tag)

    latest = artifacts.read_json(results_dir / "latest.json")
    assert latest["retrieval_trace_path"].endswith("/retrieval_trace.jsonl")
    assert latest["retrieval_summary_path"].endswith("/retrieval_summary.json")

    legacy = artifacts.paths_for_tag("old")
    assert legacy.retrieval_trace == results_dir / "retrieval_trace_old.jsonl"
    assert legacy.retrieval_summary == results_dir / "retrieval_summary_old.json"


def test_retrieval_summary_rebuild_and_old_run_fallback(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "legacy"
    paths = artifacts.paths_for_tag(tag)
    append_completed_row(
        paths.retrieval_trace,
        "eval_001",
        [],
        retrieval_latency_ms=3,
        abstained=False,
        category="factual",
    )

    rebuilt = rebuild_for_tag(tag)
    assert rebuilt is not None
    assert paths.retrieval_summary.exists()
    assert rebuild_for_tag("no-trace") is None
