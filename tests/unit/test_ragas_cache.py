import json

from app.evals import ragas_cache
from app.evals import artifacts


def test_cache_key_is_stable_and_context_order_sensitive():
    sample = {
        "user_input": "question",
        "response": "answer",
        "retrieved_contexts": ["first", "second"],
        "reference": "reference",
    }

    assert ragas_cache.cache_key(sample) == ragas_cache.cache_key(sample)
    assert ragas_cache.cache_key(sample) != ragas_cache.cache_key(
        {**sample, "retrieved_contexts": ["second", "first"]}
    )


def test_put_get_stats_and_clear(tmp_path):
    db_path = tmp_path / "ragas_cache.sqlite"
    sample = {
        "user_input": "question",
        "response": "answer",
        "retrieved_contexts": ["context"],
        "reference": "reference",
    }
    key = ragas_cache.cache_key(sample)
    scores = {"faithfulness": 1.0, "context_recall": 0.5}

    assert ragas_cache.put_many([(key, sample, scores)], source_tag="run-a", path=db_path) == 1
    assert ragas_cache.get_many([key], path=db_path) == {key: scores}

    stats = ragas_cache.stats(path=db_path)
    assert stats["total"] == 1
    assert stats["by_source_tag"] == {"run-a": 1}

    assert ragas_cache.clear(path=db_path) == 1
    assert ragas_cache.stats(path=db_path)["total"] == 0


def test_seed_from_artifacts(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    results_dir.mkdir()
    run_tag = "demo"
    run_row = {
        "question": "question",
        "answer": "answer",
        "contexts": ["context"],
        "ground_truth": "reference",
        "abstained": False,
    }
    scored_row = {
        "user_input": "question",
        "faithfulness": 1.0,
        "answer_relevancy": 0.75,
        "llm_context_precision_with_reference": 0.5,
        "context_recall": 0.25,
    }
    (results_dir / f"run_{run_tag}.jsonl").write_text(json.dumps(run_row) + "\n")
    (results_dir / f"scored_{run_tag}.json").write_text(json.dumps([scored_row]))
    monkeypatch.setattr(ragas_cache.settings, "eval_results_dir", str(results_dir))

    db_path = tmp_path / "cache.sqlite"
    assert ragas_cache.seed_from_artifacts(run_tag, path=db_path) == {
        "written": 1,
        "skipped": 0,
    }
    assert ragas_cache.stats(path=db_path)["total"] == 1


def test_seed_from_bundled_artifacts(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    run_tag = "model_label_20260706_103554"
    run_dir = results_dir / "runs" / "2026-07-06" / run_tag
    run_dir.mkdir(parents=True)
    run_row = {
        "question": "question",
        "answer": "answer",
        "contexts": ["context"],
        "ground_truth": "reference",
        "abstained": False,
    }
    scored_row = {
        "user_input": "question",
        "faithfulness": 1.0,
        "answer_relevancy": 0.75,
        "llm_context_precision_with_reference": 0.5,
        "context_recall": 0.25,
    }
    (run_dir / "run.jsonl").write_text(json.dumps(run_row) + "\n")
    (run_dir / "scored.json").write_text(json.dumps([scored_row]))
    monkeypatch.setattr(ragas_cache.settings, "eval_results_dir", str(results_dir))

    db_path = tmp_path / "cache.sqlite"
    assert ragas_cache.seed_from_artifacts(run_tag, path=db_path) == {
        "written": 1,
        "skipped": 0,
    }
    assert ragas_cache.stats(path=db_path)["total"] == 1


def test_tag_from_run_path_handles_bundled_and_legacy():
    assert artifacts.tag_from_run_path("data/eval_results/runs/2026-07-06/demo/run.jsonl") == "demo"
    assert artifacts.tag_from_run_path("data/eval_results/run_mistral_20260613_191727.jsonl") == "mistral_20260613_191727"
