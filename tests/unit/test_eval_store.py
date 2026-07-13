import json

from app.eval_store import diff_runs, get_rows, get_run, get_run_logs, list_runs
from app.api.routes_evals import run_rows as api_run_rows
from app.evals import artifacts
from app.evals.diff_report import build_diff_report


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


def test_holdout_rows_and_diff_report_are_redacted(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    dataset_path = tmp_path / "eval_dataset.jsonl"
    dataset_path.write_text("dataset\n", encoding="utf-8")
    monkeypatch.setattr(artifacts.settings, "eval_dataset_path", str(dataset_path))
    tag = "holdout_20260710_010000"
    run_dir = _run_dir(results_dir, "2026-07-10", tag)
    _manifest(results_dir, [{"tag": tag, "date": "2026-07-10"}])
    _write_json(run_dir / "meta.json", {"tag": tag, "holdout": True})
    _write_jsonl(run_dir / "run.jsonl", [{
        "eval_id": "eval_081", "question": "private", "answer": "private",
        "ground_truth": "private", "expected_sources": ["constitution_1987"],
        "retrieved_sources": ["constitution_1987"], "contexts": [], "category": "factual",
        "abstained": False,
    }])
    _write_json(run_dir / "scored.json", [{
        "eval_id": "eval_081", "user_input": "private", "reference": "private",
        "faithfulness": 0.9, "answer_relevancy": 0.8,
        "llm_context_precision_with_reference": 0.7, "context_recall": 0.6,
    }])

    assert get_rows(tag) == {
        "tag": tag, "row_count": 0, "scored_count": 0, "rows": [], "holdout_redacted": True,
    }
    assert api_run_rows(tag).holdout_redacted is True
    report = build_diff_report(tag)
    text = report.read_text(encoding="utf-8")
    assert "## Per-question" not in text
    assert "private" not in text
    ledger = results_dir / "holdout_aggregate_reads.jsonl"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["access_type"] == "diff_report"
    assert entries[-1]["holdout_row_counts"] == {tag: 1}
    assert entries[-1]["eval_dataset_sha256"]
    assert entries[-1]["eval_manifest_sha256"]
    assert "git_sha" in entries[-1]


def test_holdout_aggregate_reads_are_logged_only_on_single_run_and_compare(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    dataset_path = tmp_path / "eval_dataset.jsonl"
    dataset_path.write_text("dataset\n", encoding="utf-8")
    monkeypatch.setattr(artifacts.settings, "eval_dataset_path", str(dataset_path))
    holdout = "holdout_20260710_010000"
    baseline = "baseline_20260710_000000"
    _manifest(
        results_dir,
        [
            {"tag": holdout, "date": "2026-07-10", "label": "release-check"},
            {"tag": baseline, "date": "2026-07-10"},
        ],
    )
    holdout_dir = _run_dir(results_dir, "2026-07-10", holdout)
    baseline_dir = _run_dir(results_dir, "2026-07-10", baseline)
    _write_json(holdout_dir / "meta.json", {"tag": holdout, "holdout": True, "label": "release-check"})
    for run_dir in (holdout_dir, baseline_dir):
        _write_json(
            run_dir / "summary.json",
            {"overall": {"faithfulness": 0.8}, "abstention": {"accuracy": 1.0}, "by_category": {}},
        )

    assert list_runs()
    ledger = results_dir / "holdout_aggregate_reads.jsonl"
    assert not ledger.exists()

    assert get_run(holdout)["summary"]["overall"]["faithfulness"] == 0.8
    assert diff_runs(holdout, baseline)["overall"]["candidate"]["faithfulness"] == 0.8

    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [entry["access_type"] for entry in entries] == ["single_run", "compare"]
    assert entries[0]["tags"] == [holdout]
    assert entries[0]["purpose"] == "release-check"
    assert entries[0]["holdout_row_counts"] == {holdout: None}
    assert entries[0]["eval_dataset_sha256"]
    assert entries[0]["eval_manifest_sha256"]
    assert "git_sha" in entries[0]
    assert entries[1]["tags"] == [holdout, baseline]


def test_debug_passthrough_modern_and_legacy_rows(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "modern_eval_20260711_010000"
    run_dir = _run_dir(results_dir, "2026-07-11", tag)
    _manifest(results_dir, [{"tag": tag, "date": "2026-07-11"}])
    _write_jsonl(
        run_dir / "run.jsonl",
        [
            {
                "eval_id": "eval_001",
                "question": "q1",
                "answer": "a1",
                "ground_truth": "g1",
                "category": "civil",
                "contexts": ["ctx"],
                "abstained": False,
                "split": "regression",
                "topic": "civil_code",
                "facet": "obligations",
                "profile": "default",
                "generator_model": "haiku",
                "elapsed_s": 1.5,
                "expected_sources": ["a", "b"],
                "retrieved_sources": ["b", "b", "c"],
                "cited_sources": ["b"],
                "selected_chunk_ids": ["chunk-1", "chunk-2"],
                "evidence": {"verdict": "sufficient", "method": "heuristic", "missing_facets": []},
                "corrective_retrieval": {"enabled": True, "fired": False},
                "model_choice": {"model": "haiku", "reason": "default"},
                "debug_stages": [{"name": "hybrid_retriever", "out_n": 10, "ms": 12.3}],
            },
            {
                "eval_id": "eval_002",
                "question": "q2",
                "answer": "a2",
                "ground_truth": "g2",
                "category": "civil",
                "contexts": [],
                "abstained": False,
            },
        ],
    )

    result = get_rows(tag)

    assert result is not None
    modern, legacy = result["rows"]
    assert modern["debug_stages"] == [{"name": "hybrid_retriever", "out_n": 10, "ms": 12.3}]
    assert modern["evidence"] == {"verdict": "sufficient", "method": "heuristic", "missing_facets": []}
    assert modern["expected_missing"] == ["a"]

    assert legacy["evidence"] is None
    assert legacy["debug_stages"] == []
    assert legacy["expected_sources"] == []
    assert legacy["retrieved_sources"] == []
    assert legacy["selected_chunk_ids"] == []
    assert legacy["expected_missing"] == []


def test_expected_missing_derivation(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "missing_eval_20260711_010000"
    run_dir = _run_dir(results_dir, "2026-07-11", tag)
    _manifest(results_dir, [{"tag": tag, "date": "2026-07-11"}])
    _write_jsonl(
        run_dir / "run.jsonl",
        [{
            "eval_id": "eval_001",
            "question": "q1",
            "answer": "a1",
            "expected_sources": ["a", "b"],
            "retrieved_sources": ["b", "b", "c"],
        }],
    )

    result = get_rows(tag)

    assert result["rows"][0]["expected_missing"] == ["a"]


def test_holdout_logs_redaction(tmp_path, monkeypatch):
    results_dir = tmp_path / "eval_results"
    monkeypatch.setattr(artifacts.settings, "eval_results_dir", str(results_dir))
    tag = "holdout_20260711_010000"
    run_dir = _run_dir(results_dir, "2026-07-11", tag)
    _manifest(results_dir, [{"tag": tag, "date": "2026-07-11"}])
    _write_json(run_dir / "meta.json", {
        "tag": tag, "holdout": True,
        "started_at": "2026-07-11T01:00:00+08:00", "completed_at": "2026-07-11T02:00:00+08:00",
    })

    result = get_run_logs(tag)

    assert result == {
        "tag": tag, "window": None, "entries": [], "count": 0,
        "truncated": False, "holdout_redacted": True,
    }
