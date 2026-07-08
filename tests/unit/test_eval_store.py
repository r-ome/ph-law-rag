import json

from app.eval_store import diff_runs, get_rows, get_run, list_runs
from app.evals import artifacts


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _manifest(results_dir, rows):
    _write_jsonl(results_dir / "manifest.jsonl", rows)


def _run_dir(results_dir, date, tag):
    path = results_dir / "runs" / date / tag
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_malformed_manifest_tolerance(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    results_dir.mkdir(parents=True)
    (results_dir / "manifest.jsonl").write_text(
        json.dumps({"tag": "valid", "date": "2026-07-08"}) + "\n{broken\n",
        encoding="utf-8",
    )

    assert list_runs() == [{"tag": "valid", "date": "2026-07-08"}]


def test_unmanifested_disk_run_is_unknown(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "candidate_20260708_010000"
    run_dir = _run_dir(results_dir, "2026-07-08", tag)
    _write_jsonl(run_dir / "run.jsonl", [{"question": "q", "answer": "a"}])
    _write_json(run_dir / "summary.json", {"overall": {}, "abstention": {}, "by_category": {}})
    _manifest(results_dir, [{"tag": "baseline_20260708_000000", "date": "2026-07-08"}])

    assert get_run(tag) is None
    assert get_rows(tag) is None
    assert diff_runs(tag, tag) is None


def test_unscored_rows_present_with_null_metrics(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "model_eval_20260708_010000"
    run_dir = _run_dir(results_dir, "2026-07-08", tag)
    _manifest(results_dir, [{"tag": tag, "date": "2026-07-08"}])
    _write_jsonl(
        run_dir / "run.jsonl",
        [
            {
                "question": "q1",
                "answer": "a1",
                "ground_truth": "g1",
                "category": "civil",
                "contexts": ["ctx"],
                "abstained": False,
            },
            {
                "question": "q2",
                "answer": "",
                "ground_truth": "g2",
                "category": "civil",
                "contexts": [],
                "abstained": True,
            },
        ],
    )
    _write_json(
        run_dir / "scored.json",
        [
            {
                "user_input": "q1",
                "reference": "g1",
                "faithfulness": 0.9,
                "answer_relevancy": 0.8,
                "context_recall": 0.7,
                "llm_context_precision_with_reference": 0.6,
            }
        ],
    )

    result = get_rows(tag)

    assert result is not None
    assert result["row_count"] == 2
    assert result["scored_count"] == 1
    abstained = [row for row in result["rows"] if row["abstained"]][0]
    assert abstained["faithfulness"] is None
    assert abstained["answer_relevancy"] is None
    assert abstained["context_precision"] is None
    assert abstained["context_recall"] is None


def test_category_diff_statuses(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    candidate = "candidate_20260708_010000"
    baseline = "baseline_20260708_000000"
    _manifest(
        results_dir,
        [
            {"tag": candidate, "date": "2026-07-08"},
            {"tag": baseline, "date": "2026-07-08"},
        ],
    )
    cand_dir = _run_dir(results_dir, "2026-07-08", candidate)
    base_dir = _run_dir(results_dir, "2026-07-08", baseline)
    _write_json(
        cand_dir / "summary.json",
        {
            "overall": {"faithfulness": 0.8},
            "abstention": {"accuracy": 1.0},
            "by_category": {
                "civil": {
                    "n": 1,
                    "faithfulness": 0.8,
                    "answer_relevancy": 0.7,
                    "llm_context_precision_with_reference": 0.6,
                    "context_recall": 0.5,
                }
            },
        },
    )
    _write_json(
        base_dir / "summary.json",
        {
            "overall": {"faithfulness": 0.7},
            "abstention": {"accuracy": 0.5},
            "by_category": {},
        },
    )

    result = diff_runs(candidate, baseline)

    assert result is not None
    assert result["by_category"]["civil"]["status"] == "missing_baseline"
    assert result["by_category"]["civil"]["delta"] is None
