import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.config import settings
from app.evals import generation_replay
from app.evals.integrity import (
    append_hashed_row,
    atomic_write_json,
    canonical_json,
    file_sha256,
    ordered_hash,
    paths_for,
    read_hashed_rows,
    schema_version,
    sha256,
    text_sha256,
    validate_sealed_bundle,
)
from app.pipeline.frozen_generation import prepare_prompts, rehydrate_results, replay_frozen


def _valid_replay_record() -> dict:
    selected = [
        {
            "chunk_id": "chunk-1",
            "text": "Article 1 text",
            "score": 0.9,
            "metadata": {
                "source_id": "civil_code",
                "title": "Civil Code",
                "url": "https://example.test/civil-code",
            },
        }
    ]
    context, sources, system, user = prepare_prompts(
        "What is Article 1?", rehydrate_results(selected)
    )
    return {
        "effective_question": "What is Article 1?",
        "selected_results": selected,
        "selected_context_hash": sha256(selected),
        "terminal_response": None,
        "context_block_hash": text_sha256(context),
        "source_map": sources,
        "source_map_hash": sha256(sources),
        "system_prompt": system,
        "system_prompt_hash": text_sha256(system),
        "user_prompt": user,
        "user_prompt_hash": text_sha256(user),
        "model_choice": {"model": "gemma4:e4b", "reason": "policy_default"},
        "policy": {
            "later_enacted_preference_enabled": False,
            "selfcheck_enabled": False,
        },
    }


def test_canonical_json_is_sorted_and_rejects_non_finite_values():
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_partial_jsonl_recovers_only_a_trailing_fragment(tmp_path):
    path = tmp_path / "partial.jsonl"
    append_hashed_row(path, {"schema": schema_version(), "eval_id": "one"})
    good_size = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b'{"schema":')
    assert [row["eval_id"] for row in read_hashed_rows(path, recover_trailing_fragment=True)] == ["one"]
    assert path.stat().st_size == good_size

    row = json.loads(path.read_text().strip())
    row["eval_id"] = "tampered"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="record hash mismatch"):
        read_hashed_rows(path, recover_trailing_fragment=True)


def test_row_id_filter_preserves_requested_order():
    from app.evals.dataset import load_eval_dataset

    rows = load_eval_dataset(splits=("regression",), row_ids=["eval_003", "eval_001"])
    assert [row["id"] for row in rows] == ["eval_003", "eval_001"]


def test_terminal_response_replays_without_generation():
    terminal = {
        "answer": "frozen refusal",
        "sources": [],
        "contexts": [],
        "context_sources": [],
        "abstained": True,
        "error": False,
    }
    result = replay_frozen(
        {
            "effective_question": "question",
            "selected_results": [],
            "selected_context_hash": sha256([]),
            "terminal_response": terminal,
        }
    )
    assert result["answer"] == "frozen refusal"
    assert result["generation_skipped"] is True


def test_replay_uses_the_context_and_prompts_it_validated(monkeypatch):
    from app.pipeline import frozen_generation

    record = _valid_replay_record()
    captured = {}
    original_generate_frozen = frozen_generation.generate_frozen

    def fake_generate_frozen(**kwargs):
        captured.update(kwargs)
        return original_generate_frozen(
            **kwargs,
            generate_fn=lambda system, user, model: "Answer [1]",
            build_context_fn=lambda results: pytest.fail(
                "replay rebuilt context after validating it"
            ),
        )

    monkeypatch.setattr(frozen_generation, "generate_frozen", fake_generate_frozen)
    result = frozen_generation.replay_frozen(record)
    expected_prepared = prepare_prompts(
        record["effective_question"], rehydrate_results(record["selected_results"])
    )

    assert result["answer"] == "Answer [1]"
    assert captured["prepared"] == expected_prepared


@pytest.mark.parametrize(
    "field",
    (
        "selected_context_hash",
        "context_block_hash",
        "source_map_hash",
        "system_prompt_hash",
        "user_prompt_hash",
    ),
)
def test_replay_refuses_every_frozen_identity_mismatch(field, monkeypatch):
    from app.pipeline import frozen_generation

    record = deepcopy(_valid_replay_record())
    record[field] = "tampered"
    monkeypatch.setattr(
        frozen_generation,
        "generate_frozen",
        lambda **kwargs: pytest.fail("generation ran after a replay hash mismatch"),
    )
    with pytest.raises(ValueError, match="mismatch"):
        frozen_generation.replay_frozen(record)


def test_pre_rerank_pool_hash_excludes_float_scores():
    from app.evals.frozen_contexts import _pre_rerank_pool_hash

    first = [{"stage": "fused", "candidates": [{"chunk_id": "c1", "text": "law", "score": 0.1}]}]
    second = [{"stage": "fused", "candidates": [{"chunk_id": "c1", "text": "law", "score": 99.0}]}]
    changed = [{"stage": "fused", "candidates": [{"chunk_id": "c1", "text": "changed", "score": 0.1}]}]
    assert _pre_rerank_pool_hash(first, schema_minor=0) == _pre_rerank_pool_hash(
        second, schema_minor=0
    )
    assert _pre_rerank_pool_hash(first, schema_minor=0) != _pre_rerank_pool_hash(
        changed, schema_minor=0
    )


def test_generation_bundle_replays_and_publishes_manifest(tmp_path, monkeypatch):
    from app.pipeline import runner

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    monkeypatch.setattr("app.cli.main.configure_logging", lambda: None)
    session_appends = []
    trace_writes = []
    monkeypatch.setattr(
        runner, "_append_session_turn", lambda state: session_appends.append(state)
    )
    monkeypatch.setattr(
        runner.TraceWriter, "write", lambda self, row: trace_writes.append(row)
    )
    source = paths_for(
        "retrieval-source",
        datetime(2026, 7, 14).astimezone(),
        create=True,
    )
    legacy_schema = schema_version(minor=0)
    frozen = {
        "schema": legacy_schema,
        "eval_id": "eval_001",
        "question": "Question?",
        "effective_question": "Question?",
        "selected_results": [],
        "selected_context_hash": sha256([]),
        "candidate_stages": [{"stage": "fused", "candidates": []}],
        "pre_rerank_pool_hash": None,
        "terminal_response": None,
        "model_choice": {"model": "gemma4:e4b", "reason": "policy_default"},
        "policy": {"name": "eval", "generator_model": "gemma4:e4b"},
        "ground_truth": "Truth",
        "expected_sources": [],
        "category": "definition",
        "split": "regression",
    }
    written = append_hashed_row(source.partial, frozen)
    os.replace(source.partial, source.sealed)
    source.trace.write_text("trace\n")
    source.summary.write_text("{}\n")
    publication = {
        "row_count": 1,
        "eval_ids": ["eval_001"],
        "ordered_record_hash": ordered_hash([written["record_hash"]]),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [{"eval_id": "eval_001", "hash": None}]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [{"eval_id": "eval_001", "hash": frozen["selected_context_hash"]}]
        ),
        "bundle_file_hash": file_sha256(source.sealed),
        "retrieval_trace_hash": file_sha256(source.trace),
        "retrieval_summary_hash": file_sha256(source.summary),
    }
    meta = {
        "schema": legacy_schema,
        "artifact_type": "retrieval_bundle",
        "retrieval_config": {"values": {}, "hash": sha256({})},
        "generation_config": {"hash": "generation-config"},
        "corpus_identity": {"hash": "corpus"},
        "index_identity": {"hash": "index"},
        **publication,
    }
    atomic_write_json(source.meta, meta)
    atomic_write_json(
        source.state,
        {"schema": legacy_schema, "state": "sealed", **publication},
    )
    monkeypatch.setattr(
        generation_replay,
        "replay_frozen",
        lambda record, model_override=None: {
            "answer": "Answer [1]",
            "sources": [{"source_id": "civil_code"}],
            "contexts": [],
            "context_sources": [],
            "abstained": False,
            "error": False,
        },
    )

    run_path = generation_replay.generate_bundle(
        "retrieval-source", tag="generation-run"
    )
    rows = read_hashed_rows(run_path)
    assert rows[0]["answer"] == "Answer [1]"
    assert rows[0]["selected_chunk_ids"] == []
    assert "frozen_prompt_hashes" in rows[0]
    assert (
        json.loads((run_path.parent / "meta.json").read_text())["parity_mode"]
        is True
    )
    assert generation_replay.generate_bundle(
        "retrieval-source", tag="generation-run", resume=True
    ) == run_path
    assert session_appends == []
    assert trace_writes == []
    assert (tmp_path / "manifest.jsonl").exists()
    assert json.loads((tmp_path / "latest.json").read_text())["tag"] == "generation-run"
    monkeypatch.setattr("app.evals.ragas_scorer.score", lambda rows, use_cache=True: [])
    monkeypatch.setattr("app.evals.report.print_report", lambda rows, scored: None)
    monkeypatch.setattr("app.evals.report.save_scored", lambda rows, scored, run_tag: None)
    score_result = CliRunner().invoke(app, ["eval-score", str(run_path)])
    assert score_result.exit_code == 0, score_result.output
    assert "Retrieval metrics unavailable" in score_result.output


def test_retrieval_bundle_repairs_publish_state_after_atomic_rename(
    tmp_path, monkeypatch
):
    from app.evals.integrity import validate_sealed_bundle

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    paths = paths_for("recoverable", datetime(2026, 7, 14).astimezone(), create=True)
    legacy_schema = schema_version(minor=0)
    row = {
        "schema": legacy_schema,
        "eval_id": "eval_1",
        "selected_context_hash": sha256([]),
        "candidate_stages": [{"stage": "fused", "candidates": []}],
        "pre_rerank_pool_hash": None,
    }
    written = append_hashed_row(paths.partial, row)
    os.replace(paths.partial, paths.sealed)
    paths.trace.write_text("trace\n")
    paths.summary.write_text("{}\n")
    publication = {
        "row_count": 1,
        "eval_ids": ["eval_1"],
        "ordered_record_hash": ordered_hash([written["record_hash"]]),
        "ordered_pre_rerank_pool_hash": ordered_hash(
            [{"eval_id": "eval_1", "hash": None}]
        ),
        "ordered_selected_context_hash": ordered_hash(
            [{"eval_id": "eval_1", "hash": row["selected_context_hash"]}]
        ),
        "bundle_file_hash": file_sha256(paths.sealed),
        "retrieval_trace_hash": file_sha256(paths.trace),
        "retrieval_summary_hash": file_sha256(paths.summary),
    }
    atomic_write_json(
        paths.meta,
        {
                "schema": legacy_schema,
            "artifact_type": "retrieval_bundle",
            **publication,
        },
    )
    atomic_write_json(
        paths.state,
        {"schema": legacy_schema, "state": "validating"},
    )
    validate_sealed_bundle(paths, repair_state=True)
    assert json.loads(paths.state.read_text())["state"] == "sealed"


def test_duplicate_incompatible_and_unsealed_bundles_are_rejected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))

    duplicate = tmp_path / "duplicate.jsonl"
    append_hashed_row(duplicate, {"schema": schema_version(), "eval_id": "same"})
    append_hashed_row(duplicate, {"schema": schema_version(), "eval_id": "same"})
    with pytest.raises(ValueError, match="duplicate eval_id"):
        read_hashed_rows(duplicate)

    incompatible = tmp_path / "incompatible.jsonl"
    append_hashed_row(
        incompatible,
        {
            "schema": {**schema_version(), "major": schema_version()["major"] + 1},
            "eval_id": "one",
        },
    )
    with pytest.raises(ValueError, match="incompatible artifact schema major"):
        read_hashed_rows(incompatible)

    unsealed = paths_for("unsealed", datetime(2026, 7, 14).astimezone(), create=True)
    unsealed.sealed.write_text("")
    unsealed.trace.write_text("trace\n")
    unsealed.summary.write_text("{}\n")
    atomic_write_json(
        unsealed.meta,
        {"schema": schema_version(), "artifact_type": "retrieval_bundle"},
    )
    atomic_write_json(
        unsealed.state,
        {"schema": schema_version(), "state": "validating"},
    )
    with pytest.raises(ValueError, match="not publishable"):
        validate_sealed_bundle(unsealed)


def test_retrieval_only_capture_has_no_generation_or_finalize_side_effects(
    tmp_path, monkeypatch
):
    from app.evals import retrieval_runner
    from app.evals.integrity import read_json, validate_sealed_bundle
    from app.observability.context import current_trace_collector
    from app.pipeline.state import EvidenceReport, ModelChoice
    from app.retriever.context_selection import SelectionResult
    from app.retriever.types import RetrievalResult

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "missing.db"))
    monkeypatch.setattr(settings, "bm25_path", str(tmp_path / "missing-bm25"))
    monkeypatch.setattr(
        retrieval_runner,
        "_qdrant_collection_identity",
        lambda: {"available": True, "points_count": 1, "hash": "qdrant"},
    )
    target = {
        "eval_id": "eval_x",
        "match_mode": "exact",
        "targets": [
            {"source_id": "civil_code", "provision_id": "civil_code:article:1"}
        ],
    }
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {"eval_x": target},
    )

    result = RetrievalResult(
        "chunk-1",
        "Article 1 text",
        0.9,
        {
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1",
            "title": "Civil Code",
            "url": "https://example.test",
        },
    )

    def fake_prepare(state, *, query_separation_arm="original_only"):
        state.selection = SelectionResult([result], [result], [result])
        state.evidence = EvidenceReport("sufficient", "min_chunks", [], {})
        collector = current_trace_collector()
        collector.candidates("dense", [result], score_field="dense_score")
        collector.candidates("sparse", [result], score_field="sparse_score")
        collector.candidates("fused", [result], score_field="fused_score")
        collector.candidates(
            "fused",
            [result],
            query_variant="combined",
            pool_role="pre_rerank_pool",
            score_field="fused_score",
        )
        collector.candidates("reranked", [result], score_field="rerank_score")
        collector.candidates("expanded", [result], selected_ids={"chunk-1"})
        collector.candidates("selected", [result], selected_ids={"chunk-1"})

    def fake_route(state):
        state.model_choice = ModelChoice("gemma4:e4b", "policy_default")

    monkeypatch.setattr(retrieval_runner, "prepare_answer_state", fake_prepare)
    monkeypatch.setattr("app.pipeline.stages.route_model", fake_route)
    monkeypatch.setattr(
        "app.pipeline.stages.generate_answer",
        lambda state: pytest.fail("generation ran during retrieval-only capture"),
    )
    monkeypatch.setattr(
        "app.pipeline.runner._finalize",
        lambda **kwargs: pytest.fail("_finalize ran during retrieval-only capture"),
    )
    monkeypatch.setattr(
        "app.retriever.reranker.release_retrieval_models",
        lambda: {"attempted": True, "warning": "best-effort unload warning"},
    )

    sealed = retrieval_runner.retrieve_rows(
        [
            {
                "id": "eval_x",
                "question": "What is Article 1?",
                "ground_truth": "Truth",
                "expected_sources": ["civil_code"],
                "category": "definition",
                "split": "regression",
            }
        ],
        tag="retrieval-run",
    )
    frozen, meta = validate_sealed_bundle(paths_for("retrieval-run"))
    assert sealed.exists()
    assert frozen[0]["retrieval_target_present"] is True
    assert frozen[0]["pre_rerank_pool_hash"]
    assert meta["retrieval_config"]["shared_hash"] != "local"
    assert meta["memory_release"]["warning"] == "best-effort unload warning"
    assert meta["capture_consistency"]["matched"] is True
    assert "candidate_stages" not in frozen[0]["retrieval_trace"]
    assert read_json(sealed.parent / "retrieval_summary.json")["available"] is True


def test_retrieval_capture_refuses_to_seal_after_corpus_drift(tmp_path, monkeypatch):
    from app.evals import retrieval_runner
    from app.evals.integrity import read_json
    from app.observability.context import current_trace_collector

    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    target = {"eval_id": "eval_x", "match_mode": "exact", "targets": []}
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {"eval_x": target},
    )

    def storage(corpus_hash: str) -> dict:
        return {
            "corpus_identity": {"hash": corpus_hash},
            "bm25_identity": {"hash": "bm25"},
            "qdrant_identity": {"hash": "qdrant"},
            "index_identity": {"hash": f"index-{corpus_hash}"},
        }

    identities = iter((storage("start"), storage("end")))
    monkeypatch.setattr(retrieval_runner, "_storage_identities", lambda: next(identities))

    def terminal_prepare(state, *, query_separation_arm="original_only"):
        collector = current_trace_collector()
        collector.candidates("dense", [])
        collector.candidates("sparse", [])
        collector.candidates("fused", [])
        collector.candidates(
            "fused",
            [],
            query_variant="combined",
            pool_role="pre_rerank_pool",
        )
        collector.candidates("reranked", [])
        collector.candidates("expanded", [])
        collector.candidates("selected", [])
        state.response = {
            "answer": "frozen abstention",
            "sources": [],
            "contexts": [],
            "context_sources": [],
            "abstained": True,
            "error": False,
        }

    monkeypatch.setattr(retrieval_runner, "prepare_answer_state", terminal_prepare)
    monkeypatch.setattr(
        "app.retriever.reranker.release_retrieval_models",
        lambda: {"attempted": True, "warning": None},
    )
    row = {
        "id": "eval_x",
        "question": "Question?",
        "ground_truth": "Truth",
        "expected_sources": [],
        "category": "abstention",
        "split": "regression",
    }

    with pytest.raises(ValueError, match="drifted during capture"):
        retrieval_runner.retrieve_rows([row], tag="drifted")

    paths = paths_for("drifted")
    state = read_json(paths.state)
    assert state["state"] == "failed"
    assert state["capture_consistency"]["matched"] is False
    assert state["capture_consistency"]["changed"] == [
        "corpus_identity",
        "index_identity",
    ]
    assert paths.partial.exists()
    assert not paths.sealed.exists()


@pytest.mark.parametrize(
    ("abstained", "error"),
    ((False, False), (True, False), (False, True)),
)
def test_normal_finalization_writes_session_and_trace_exactly_once(
    abstained, error, monkeypatch
):
    from app.pipeline import runner

    session_appends = []
    trace_writes = []
    monkeypatch.setattr(runner.settings, "trace_logging_enabled", True)
    monkeypatch.setattr(runner, "_ensure_session", lambda session_id: None)
    monkeypatch.setattr(
        runner, "_append_session_turn", lambda state: session_appends.append(state)
    )
    monkeypatch.setattr(
        runner.TraceWriter, "write", lambda self, row: trace_writes.append(row)
    )

    def terminal_prepare(state, **kwargs):
        state.response = {
            "answer": "terminal response",
            "sources": [],
            "contexts": [],
            "context_sources": [],
            "abstained": abstained,
            "error": error,
        }

    monkeypatch.setattr(runner, "prepare_answer_state", terminal_prepare)
    response, trace = runner.run_answer(
        "Question?", session_id="session-1", trace=True
    )

    assert response["abstained"] is abstained
    assert response["error"] is error
    assert len(session_appends) == 1
    assert len(trace_writes) == 1
    assert trace == trace_writes[0]


def test_holdout_rejected_before_artifact_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    monkeypatch.setattr("app.cli.main.configure_logging", lambda: None)
    result = CliRunner().invoke(
        app,
        ["eval-retrieve", "--split", "holdout", "--tag", "forbidden"],
    )
    assert result.exit_code != 0
    assert "holdout is sealed" in result.output
    assert not (tmp_path / "runs").exists()


def test_generation_replay_import_isolated_from_retrieval_stack():
    code = (
        "import hashlib, sys; import app.evals.generation_replay as replay; "
        "replay.replay_frozen({'effective_question':'q','selected_results':[],"
        "'selected_context_hash':hashlib.sha256(b'[]').hexdigest(),"
        "'terminal_response':{'answer':'skip','sources':[],'contexts':[],"
        "'context_sources':[],'abstained':True,'error':False}}); "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    probe = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert probe.returncode == 0, probe.stderr
    loaded = set(probe.stdout.splitlines())
    forbidden = (
        "torch",
        "sentence_transformers",
        "app.pipeline.runner",
        "app.pipeline.stages",
        "app.retriever.reranker",
        "app.retriever.context_selection",
        "qdrant_client",
        "llama_index.retrievers.bm25",
    )
    for module in forbidden:
        assert not any(name == module or name.startswith(module + ".") for name in loaded)


def test_sweep_score_provenance_never_labels_fused_score_as_distance():
    from scripts.trace_topk_sweep import StageHit, _hit_payload

    hit = StageHit(True, rank=1, score=0.75, distance=None)
    fused = _hit_payload(hit, score_field="fused_score")
    dense = _hit_payload(
        StageHit(True, rank=1, score=0.75, distance=0.25),
        score_field="dense_score",
        include_distance=True,
    )
    assert fused["score_provenance"] == "fused_score"
    assert "distance" not in fused
    assert dense["distance_provenance"] == "1 - qdrant_cosine_similarity"
