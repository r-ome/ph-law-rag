import json
from datetime import datetime

import pytest

from app.config import settings
from app.evals import artifacts
from app.evals.paired_aggregate import (
    _assert_no_disclosure,
    build_paired_aggregate,
    printable_summary,
)

pytestmark = pytest.mark.unit


def _row(eval_id: str, *, rendered_tokens: int, adaptive: bool, abstained: bool = False):
    return {
        "eval_id": eval_id,
        "question": f"secret question {eval_id}",
        "answer": f"secret answer {eval_id}",
        "contexts": [] if abstained else [f"secret context {eval_id}"],
        "ground_truth": f"secret truth {eval_id}",
        "category": "factual",
        "split": "holdout",
        "abstained": abstained,
        "selected_chunk_ids": [f"c-{eval_id}", "kept"] if adaptive else [f"c-{eval_id}", "drop"],
        "debug_stages": [
            {
                "name": "adaptive_context",
                "packaging_pool_semantic_hash": f"semantic-{eval_id}",
                "packaging_pool_full_hash": f"full-{eval_id}",
                "enabled": adaptive,
                "rendered_tokens": rendered_tokens,
                "cap": 7 if not adaptive else 11,
                "stop_reason": "fixed_control" if not adaptive else "stabilized",
                "signals": {
                    "accepted_legal_rewrite": False,
                    "synthesis_detected": adaptive,
                    "coverage_uncertain": False,
                },
            }
        ],
    }


def _scored(eval_id: str, faithfulness: float, recall: float):
    return {
        "eval_id": eval_id,
        "user_input": f"secret question {eval_id}",
        "response": f"secret answer {eval_id}",
        "retrieved_contexts": [f"secret context {eval_id}"],
        "reference": f"secret truth {eval_id}",
        "faithfulness": faithfulness,
        "answer_relevancy": 0.9,
        "llm_context_precision_with_reference": 0.8,
        "context_recall": recall,
    }


def _write_run(tag: str, rows, scored, *, adaptive: bool, holdout: bool = True):
    paths = artifacts.create_run_paths(tag, datetime(2026, 7, 17, 12, 0, 0))
    paths.run.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    artifacts.write_json(paths.scored, scored)
    active_config = {
        "profile": "eval",
        "llm_model": "gemma4:e4b",
        "qdrant_collection": "ph_law",
        "embedding_model": "qwen",
        "adaptive_context_enabled": adaptive,
        "adaptive_context_base_cap": 7,
        "adaptive_context_uncertain_cap": 11,
        "adaptive_context_multifacet_cap": 11,
        "adaptive_context_token_target": 2400,
    }
    meta = {
        "tag": tag,
        "date": "2026-07-17",
        "model": "gemma4:e4b",
        "generator_model": "gemma4:e4b",
        "question_count": len(rows),
        "scored_count": len(scored),
        "git_sha": "abc123",
        "holdout": holdout,
        "active_config": active_config,
        "dataset_identity": {"sha256": "dataset"},
        "corpus_identity": {"hash": "corpus"},
        "index_identity": {"hash": "index"},
        "storage_consistency": {"matched": True},
    }
    artifacts.save_meta(tag, meta)
    return paths


def test_paired_aggregate_is_aggregate_only_and_logs_single_holdout_read(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    baseline_rows = [
        _row("eval_132", rendered_tokens=1000, adaptive=False),
        _row("eval_133", rendered_tokens=1000, adaptive=False),
    ]
    candidate_rows = [
        _row("eval_132", rendered_tokens=900, adaptive=True),
        _row("eval_133", rendered_tokens=900, adaptive=True),
    ]
    _write_run(
        "baseline",
        baseline_rows,
        [_scored("eval_132", 0.8, 0.8), _scored("eval_133", 0.8, 0.8)],
        adaptive=False,
    )
    _write_run(
        "candidate",
        candidate_rows,
        [_scored("eval_132", 0.8, 0.8), _scored("eval_133", 0.8, 0.8)],
        adaptive=True,
    )

    artifact = build_paired_aggregate("baseline", "candidate", tag="paired")
    summary = printable_summary(artifact)
    artifact_text = json.dumps(artifact, ensure_ascii=False)
    summary_text = json.dumps(summary, ensure_ascii=False)

    assert artifact["verdict"] == "eligible_for_release_decision"
    assert artifact["benefit"]["rendered_token_reduction"] == 0.1
    assert "eval_132" not in artifact_text
    assert "secret question" not in artifact_text
    assert "secret context" not in artifact_text
    assert "eval_132" not in summary_text
    assert "secret question" not in summary_text
    ledger = (tmp_path / "holdout_aggregate_reads.jsonl").read_text(encoding="utf-8")
    assert ledger.count("\n") == 1
    assert "eval_132" not in ledger
    assert "secret question" not in ledger


def test_paired_aggregate_fails_closed_on_semantic_pool_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    baseline_rows = [_row("eval_132", rendered_tokens=1000, adaptive=False)]
    candidate_rows = [_row("eval_132", rendered_tokens=900, adaptive=True)]
    candidate_rows[0]["debug_stages"][0]["packaging_pool_semantic_hash"] = "different"
    _write_run(
        "baseline",
        baseline_rows,
        [_scored("eval_132", 0.8, 0.8)],
        adaptive=False,
    )
    _write_run(
        "candidate",
        candidate_rows,
        [_scored("eval_132", 0.8, 0.8)],
        adaptive=True,
    )

    with pytest.raises(ValueError, match="packaging_pool_semantic_hash mismatch"):
        build_paired_aggregate("baseline", "candidate", tag="paired")


def test_disclosure_guard_category_is_exact_not_substring():
    rows = [
        {
            "eval_id": "eval_132",
            "question": "secret question",
            "answer": "secret answer",
            "ground_truth": "secret truth",
            "category": "synthesis",
            "contexts": ["secret context"],
        }
    ]

    _assert_no_disclosure(
        {
            "execution": {
                "candidate_activation_counts": {"synthesis_detected": 1},
                "labeled_synthesis_holdout_n": 4,
            }
        },
        rows,
        stdout=json.dumps({"execution": {"candidate_activation_counts": {"synthesis_detected": 1}}}),
    )

    with pytest.raises(AssertionError, match="disclosure guard"):
        _assert_no_disclosure({"category": "synthesis"}, rows)

    with pytest.raises(AssertionError, match="disclosure guard"):
        _assert_no_disclosure({"tag": "safe"}, rows, stdout='{"row": "eval_132"}')
