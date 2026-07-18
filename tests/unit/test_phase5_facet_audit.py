import json
import sys
import types
from datetime import datetime

import pytest

from app.config import settings
from app.evals import facet_audit
from app.evals.integrity import (
    _pre_rerank_pool_hash,
    append_hashed_row,
    atomic_write_json,
    file_sha256,
    ordered_hash,
    paths_for,
    read_hashed_rows,
    retrieval_config_identity,
    schema_version,
    sha256,
)
import os

pytestmark = pytest.mark.unit


def _shared_values() -> dict:
    return {"knob": "value"}


def _pool_candidate(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.5,
        "metadata": {"source_id": "s", "provision_id": f"s:section:{chunk_id}"},
    }


def _write_bundle(
    tag: str,
    *,
    eval_id: str = "eval_001",
    question: str = "What is theft under the Revised Penal Code?",
    pool_candidates: list[dict] | None = None,
    selected: list[dict] | None = None,
    holdout: bool = False,
    row_split: str = "regression",
    splits_meta: list[str] | None = None,
):
    schema = schema_version()
    pool_candidates = pool_candidates or [
        _pool_candidate("chunk-1", "Theft is the taking of personal property with intent to gain."),
        _pool_candidate("chunk-2", "The penalty for theft depends on the value of the property taken."),
    ]
    selected = selected if selected is not None else [pool_candidates[0]]
    stages = [
        {
            "stage": "fused",
            "query_variant": "combined",
            "pool_role": "pre_rerank_pool",
            "candidates": pool_candidates,
        },
        {"stage": "selected", "query_variant": "original", "candidates": selected},
    ]
    pre_hash = _pre_rerank_pool_hash(stages, schema_minor=1)
    record = {
        "schema": schema,
        "eval_id": eval_id,
        "question": question,
        "effective_question": question,
        "split": row_split,
        "candidate_stages": stages,
        "pre_rerank_pool_hash": pre_hash,
        "selected_results": selected,
        "selected_context_hash": sha256(selected),
    }
    paths = paths_for(tag, datetime(2026, 7, 17).astimezone(), create=True)
    written = append_hashed_row(paths.partial, record)
    os.replace(paths.partial, paths.sealed)
    paths.trace.write_text("", encoding="utf-8")
    paths.summary.write_text("{}\n", encoding="utf-8")
    publication = {
        "row_count": 1,
        "eval_ids": [eval_id],
        "ordered_record_hash": ordered_hash([written["record_hash"]]),
        "ordered_pre_rerank_pool_hash": ordered_hash([{"eval_id": eval_id, "hash": pre_hash}]),
        "ordered_selected_context_hash": ordered_hash(
            [{"eval_id": eval_id, "hash": record["selected_context_hash"]}]
        ),
        "bundle_file_hash": file_sha256(paths.sealed),
        "retrieval_trace_hash": file_sha256(paths.trace),
        "retrieval_summary_hash": file_sha256(paths.summary),
    }
    shared = _shared_values()
    retrieval_config = retrieval_config_identity(shared, arm="original_only")
    meta = {
        "schema": schema,
        "artifact_type": "retrieval_bundle",
        "tag": tag,
        "holdout": holdout,
        "splits": splits_meta if splits_meta is not None else (["holdout"] if holdout else ["regression"]),
        "dataset_identity": {"row_count": 1, "eval_ids": [eval_id], "ordered_row_hash": "rows"},
        "targets_identity": {"ordered_target_hash": "targets"},
        "corpus_identity": {"hash": "corpus"},
        "index_identity": {"hash": "index"},
        "retrieval_config": retrieval_config,
        "generation_config": {"values": {}, "hash": sha256({})},
        **publication,
    }
    atomic_write_json(paths.meta, meta)
    atomic_write_json(paths.state, {"schema": schema, "state": "sealed", **publication})
    return paths


class _RaisingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("network call attempted in default (zero-call) mode")


class _FakeClient:
    call_count = 0

    def __init__(self, *args, **kwargs):
        assert kwargs.get("max_retries") == 0
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        _FakeClient.call_count += 1
        text = (
            "FACETS: elements of theft; penalty for theft\n"
            "PRESENT: elements of theft\n"
            "MISSING: penalty for theft\n"
            "VERDICT: partial"
        )
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)])


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    _FakeClient.call_count = 0
    yield


def test_default_mode_makes_zero_network_calls_and_reports_misses(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_RaisingClient))
    _write_bundle("bundle")

    result = facet_audit.run_facet_audit("bundle", output_tag="out")

    assert result["mode"] == "dry_run"
    assert result["cache_misses"] == 1
    assert result["cache_hits"] == 0
    assert "1 uncached Haiku calls required" in result["message"]
    assert not paths_for("out").root.exists()


def test_authorization_flag_required_to_make_calls(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_FakeClient))
    _write_bundle("bundle")

    result = facet_audit.run_facet_audit(
        "bundle", output_tag="out", authorize_paid_calls=True
    )

    assert result["mode"] == "sealed"
    assert _FakeClient.call_count == 1
    assert result["cache"]["calls_made"] == 1
    assert result["cache"]["misses"] == 1

    # Second run against a fresh output tag hits the cache: zero new calls,
    # even without --authorize-paid-calls.
    result2 = facet_audit.run_facet_audit("bundle", output_tag="out2")
    assert result2["mode"] == "sealed"
    assert _FakeClient.call_count == 1
    assert result2["cache"]["hits"] == 1
    assert result2["cache"]["misses"] == 0


def test_cache_hit_never_recalls(monkeypatch):
    monkeypatch.setattr(facet_audit.facet_checker, "_call_haiku", lambda prompt, model: "FACETS: x\nPRESENT: x\nMISSING: none\nVERDICT: sufficient")
    decision = facet_audit.call_and_cache("prompt-a", model="claude-haiku-4-5")
    assert decision.cache_status == "miss_written"

    def _fail(*args, **kwargs):
        pytest.fail("paid call retried on cache hit")

    monkeypatch.setattr(facet_audit.facet_checker, "_call_haiku", _fail)
    hit = facet_audit.cached_decision("prompt-a", model="claude-haiku-4-5")
    assert hit is not None
    assert hit.cache_status == "hit"
    assert hit.verdict == "sufficient"


def test_pending_marker_recovers_without_recalling(monkeypatch):
    key = facet_audit.facet_audit_cache_key("prompt-b", model="claude-haiku-4-5")
    cache_dir = facet_audit._cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    pending_path = cache_dir / f"{key}.pending.json"
    facet_audit._write_pending(pending_path, key)

    monkeypatch.setattr(
        facet_audit.facet_checker, "_call_haiku", lambda *a, **k: pytest.fail("paid call retried")
    )
    decision = facet_audit.call_and_cache("prompt-b", model="claude-haiku-4-5")
    assert decision.cache_status == "pending_recovered"
    assert decision.operational_fallback is True
    assert not pending_path.exists()


def test_malformed_final_record_falls_back_without_a_paid_call(monkeypatch):
    key = facet_audit.facet_audit_cache_key("prompt-c", model="claude-haiku-4-5")
    cache_dir = facet_audit._cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = cache_dir / f"{key}.json"
    final_path.write_text("not json", encoding="utf-8")

    monkeypatch.setattr(
        facet_audit.facet_checker, "_call_haiku", lambda *a, **k: pytest.fail("paid call retried")
    )
    decision = facet_audit.call_and_cache("prompt-c", model="claude-haiku-4-5")
    assert decision.cache_status == "pending_recovered"


def test_fail_open_on_malformed_judge_output(monkeypatch):
    monkeypatch.setattr(facet_audit.facet_checker, "_call_haiku", lambda prompt, model: "not a valid response")
    decision = facet_audit.call_and_cache("prompt-d", model="claude-haiku-4-5")
    assert decision.operational_fallback is True
    assert decision.verdict == "sufficient"
    assert decision.missing == []


def test_fail_open_on_exception(monkeypatch):
    def _boom(prompt, model):
        raise RuntimeError("boom")

    monkeypatch.setattr(facet_audit.facet_checker, "_call_haiku", _boom)
    decision = facet_audit.call_and_cache("prompt-e", model="claude-haiku-4-5")
    assert decision.operational_fallback is True
    assert decision.verdict == "sufficient"
    assert decision.judge_error == "RuntimeError: boom"


@pytest.mark.parametrize(
    "holdout,splits_meta,row_split",
    [
        (True, ["holdout"], "holdout"),
        (False, ["holdout", "regression"], "regression"),
        (False, ["regression"], "holdout"),
    ],
)
def test_holdout_rejected_fail_closed_before_any_activity(
    tmp_path, holdout, splits_meta, row_split
):
    _write_bundle("bundle", holdout=holdout, splits_meta=splits_meta, row_split=row_split)

    with pytest.raises(PermissionError):
        facet_audit.run_facet_audit("bundle", output_tag="out")

    # No cache dir and no output artifact created.
    assert not facet_audit._cache_dir().exists()
    assert not paths_for("out").root.exists()


def test_write_once_output_tag(monkeypatch):
    monkeypatch.setattr(facet_audit.facet_checker, "_call_haiku", lambda prompt, model: "FACETS: x\nPRESENT: x\nMISSING: none\nVERDICT: sufficient")
    _write_bundle("bundle")
    facet_audit.run_facet_audit("bundle", output_tag="out", authorize_paid_calls=True)

    with pytest.raises(FileExistsError):
        facet_audit.run_facet_audit("bundle", output_tag="out", authorize_paid_calls=True)


def test_reconstruct_selected_context_refuses_hash_mismatch():
    row = {
        "eval_id": "eval_001",
        "selected_results": [{"chunk_id": "a", "text": "t", "score": 1.0, "metadata": {}}],
        "selected_context_hash": "deadbeef",
    }
    with pytest.raises(ValueError, match="selected_context_hash mismatch"):
        facet_audit.reconstruct_selected_context(row)


def test_classification_absent_present_selected_and_dropped():
    pool = [
        facet_audit.PoolChunk("chunk-1", "Theft is the taking of personal property with intent to gain."),
        facet_audit.PoolChunk("chunk-2", "The penalty for theft depends on the value of the property taken."),
    ]
    missing = [
        "elements of estafa",  # absent from pool entirely
        "taking of personal property with intent to gain",  # present and selected -> not (b)
        "penalty for theft",  # present in pool but not selected -> (b)
    ]
    selected_ids = {"chunk-1"}

    results = facet_audit.classify_missing_facets(missing, pool, selected_ids)
    by_facet = {entry["facet"]: entry for entry in results}

    assert by_facet["elements of estafa"]["class"] == "absent_from_pool"
    assert (
        by_facet["taking of personal property with intent to gain"]["class"]
        == "present_in_selected"
    )
    assert by_facet["penalty for theft"]["class"] == "dropped_by_selection"
    assert by_facet["penalty for theft"]["best_chunk_id"] == "chunk-2"


def test_classification_hash_mismatch_refuses_row(monkeypatch):
    stages = [
        {
            "stage": "fused",
            "query_variant": "combined",
            "pool_role": "pre_rerank_pool",
            "candidates": [
                {"chunk_id": "chunk-1", "text_hash": "not-the-real-hash", "score": 0.5, "metadata": {}}
            ],
        }
    ]

    monkeypatch.setattr(
        "app.db.get_chunks_by_ids",
        lambda ids: [{"chunk_id": "chunk-1", "text": "actual corpus text"}],
    )
    with pytest.raises(ValueError, match="text hash mismatch"):
        facet_audit._pool_chunks_from_snapshot(stages, eval_id="eval_001")


def test_watch_rows_reported_when_present(monkeypatch):
    monkeypatch.setattr(
        facet_audit.facet_checker,
        "_call_haiku",
        lambda prompt, model: (
            "FACETS: bail exception\n"
            "PRESENT: none\n"
            "MISSING: bail exception\n"
            "VERDICT: partial"
        ),
    )
    _write_bundle("bundle", eval_id="eval_129")
    result = facet_audit.run_facet_audit("bundle", output_tag="out", authorize_paid_calls=True)

    assert result["watch_rows"]["eval_129"]["present_in_bundle"] is True
    assert result["watch_rows"]["eval_129"]["verdict"] == "partial"
    assert result["watch_rows"]["eval_124"]["present_in_bundle"] is False
