import json
from datetime import datetime

import pytest

from app.config import settings
from app.evals import artifacts
from app.evals.phase4_single_pass import run_phase4_single_pass
from app.retriever.adaptive_context import select_adaptive_context
from app.retriever.context_selection import SelectionResult
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _result(chunk_id: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        text=f"text {chunk_id}",
        score=1.0,
        metadata={
            "source_id": "civil_code",
            "doc_id": "civil-doc",
            "title": "Civil Code",
            "url": "https://example.test/civil",
            "provision_id": "civil:article:1",
            "structure_path": "BOOK I > TITLE I",
            "unit_type": "article",
            "unit_label": "Article 1",
        },
    )


def test_single_pass_derives_both_arms_from_one_packaging_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    dataset_path = tmp_path / "eval_dataset.jsonl"
    dataset_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "eval_dataset_path", str(dataset_path))
    monkeypatch.setattr(
        "app.evals.phase4_single_pass.load_eval_dataset",
        lambda splits: [
            {
                "id": "eval_x",
                "question": "What is the rule?",
                "ground_truth": "ground truth",
                "category": "factual",
                "split": "dev",
            }
        ],
    )

    pool = [_result(str(index)) for index in range(5)]

    def fake_prepare(state, **_kwargs):
        state.selection = SelectionResult(
            retrieved=list(pool),
            pre_expansion=list(pool),
            selected=list(pool),
        )

    monkeypatch.setattr(
        "app.evals.phase4_single_pass.prepare_answer_state",
        fake_prepare,
    )
    monkeypatch.setattr(
        "app.evals.phase4_single_pass.stages.route_model",
        lambda state: None,
    )
    monkeypatch.setattr(
        "app.evals.phase4_single_pass.generate_frozen",
        lambda **kwargs: {
            "answer": "answer",
            "contexts": [item["text"] for item in kwargs["selected"]],
            "abstained": False,
            "sources": [],
            "context_sources": [],
        },
    )
    identity = {
        "corpus_identity": {"hash": "corpus"},
        "bm25_identity": {"hash": "bm25"},
        "qdrant_identity": {"hash": "qdrant"},
        "index_identity": {"hash": "index"},
    }
    monkeypatch.setattr(
        "app.evals.phase4_single_pass._storage_identities",
        lambda: identity,
    )
    monkeypatch.setattr("app.evals.phase4_single_pass._git_sha", lambda: "abc123")
    selector_kwargs = {}

    def capturing_selector(results, **kwargs):
        selector_kwargs.update(kwargs)
        return select_adaptive_context(results, **kwargs)

    monkeypatch.setattr(
        "app.evals.phase4_single_pass.select_adaptive_context",
        capturing_selector,
    )

    captured = {}

    def fake_build(baseline_tag, candidate_tag, **kwargs):
        captured["baseline_tag"] = baseline_tag
        captured["candidate_tag"] = candidate_tag
        captured["kwargs"] = kwargs
        baseline = [
            json.loads(line)
            for line in artifacts.existing_path(baseline_tag, "run", required=True)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        candidate = [
            json.loads(line)
            for line in artifacts.existing_path(candidate_tag, "run", required=True)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        captured["baseline"] = baseline
        captured["candidate"] = candidate
        return {"verdict": "not_scored", "holdout": kwargs["holdout"]}

    monkeypatch.setattr(
        "app.evals.phase4_single_pass.build_paired_aggregate",
        fake_build,
    )

    artifact = run_phase4_single_pass(tag="phase4-dev", splits=("dev",))

    assert artifact == {"verdict": "not_scored", "holdout": False}
    assert captured["baseline_tag"] == "phase4-dev-baseline"
    assert captured["candidate_tag"] == "phase4-dev-candidate"
    assert captured["kwargs"]["tag"] == "phase4-dev"
    baseline_stage = captured["baseline"][0]["debug_stages"][0]
    candidate_stage = captured["candidate"][0]["debug_stages"][0]
    assert baseline_stage["packaging_pool_semantic_hash"] == candidate_stage[
        "packaging_pool_semantic_hash"
    ]
    assert baseline_stage["stop_reason"] == "fixed_control"
    assert len(captured["baseline"][0]["selected_chunk_ids"]) == 5
    assert len(captured["candidate"][0]["selected_chunk_ids"]) == 4
    assert selector_kwargs["base_cap"] == 7
    assert selector_kwargs["uncertain_cap"] == 11
    assert selector_kwargs["multifacet_cap"] == 11
    assert selector_kwargs["token_target"] == 2400


def test_single_pass_rejects_reused_arm_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    artifacts.create_run_paths("phase4-dev-baseline", datetime(2026, 7, 17))

    with pytest.raises(FileExistsError, match="phase4-dev-baseline"):
        run_phase4_single_pass(tag="phase4-dev", splits=("dev",))
