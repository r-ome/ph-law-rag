import inspect
import importlib
import json
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from app.cli.main import app
from app.config import settings
from app.evals import retrieval_runner
from app.evals.frozen_contexts import make_record, seal
from app.evals.integrity import (
    _legal_query_separation_semantic_input_hash,
    append_hashed_row,
    paths_for,
    query_separation_identity,
    read_hashed_rows,
    read_json,
    sha256,
)
from app.evals.retrieval_trace import candidate_count_metadata
from app.observability.context import (
    TraceCollector,
    capture_candidates,
    current_trace_collector,
    trace_context,
)
from app.pipeline import runner, stages
from app.pipeline.policy import resolve_policy
from app.pipeline.state import AnswerState
from app.retriever import context_selection, hybrid_retriever as hybrid_module
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult


pytestmark = pytest.mark.unit


def _knobs(**updates) -> RetrievalKnobs:
    base = RetrievalKnobs.from_settings()
    values = {
        "dense_top_k": 2,
        "sparse_top_k": 2,
        "rerank_top_n": 4,
        "edge_expansion_enabled": False,
        "parent_expansion_enabled": False,
        "prefer_operative_enabled": False,
        "consolidated_dedup_enabled": False,
        "query_decomposition_enabled": False,
        "subquery_packaging_enabled": False,
    }
    values.update(updates)
    return replace(base, **values)


def _accepted(source_query: str, legal_query: str):
    identity = query_separation_identity()
    return SimpleNamespace(
        source_query=source_query,
        legal_query=legal_query,
        confidence="high",
        status="accepted",
        parser_outcome="valid",
        fallback_reason=None,
        model=identity["model"],
        prompt_version=identity["prompt_version"],
        prompt_hash=identity["prompt_hash"],
        raw_output_hash="a" * 64,
        call_latency_ms=12.5,
        cache_key="b" * 64,
        cache_status="hit",
    )


def _result(chunk_id: str, score: float, lane: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id,
        f"text-{chunk_id}",
        score,
        {"source_id": lane, "marker": lane},
    )


def test_cli_arm_reaches_retrieval_runner(monkeypatch, tmp_path):
    captured = {}
    rows = [
        {
            "id": "eval_034",
            "question": "plain-language question",
            "split": "dev",
        }
    ]
    monkeypatch.setattr("app.cli.main.configure_logging", lambda: None)
    monkeypatch.setattr("app.evals.dataset.load_eval_dataset", lambda *_a, **_k: rows)

    def fake_retrieve(received, **kwargs):
        captured["rows"] = received
        captured.update(kwargs)
        return tmp_path / "frozen_contexts.jsonl"

    monkeypatch.setattr(retrieval_runner, "retrieve_rows", fake_retrieve)
    result = CliRunner().invoke(
        app,
        [
            "eval-retrieve",
            "--split",
            "dev",
            "--row-id",
            "eval_034",
            "--tag",
            "checkpoint4",
            "--legal-query-separation",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["rows"] == rows
    assert captured["query_separation_arm"] == "original_plus_rewrite"


def test_runtime_arm_identity_changes_only_query_separation():
    policy = resolve_policy().policy
    original, original_generation = retrieval_runner._config_identities(
        policy,
        query_separation_arm="original_only",
    )
    rewritten, rewritten_generation = retrieval_runner._config_identities(
        policy,
        query_separation_arm="original_plus_rewrite",
    )
    assert original["shared_values"] == rewritten["shared_values"]
    assert original["shared_hash"] == rewritten["shared_hash"]
    assert original_generation == rewritten_generation
    assert original["query_separation"]["arm"] == "original_only"
    assert rewritten["query_separation"]["arm"] == "original_plus_rewrite"
    assert original["full_hash"] != rewritten["full_hash"]


def test_run_answer_is_structurally_original_only():
    assert "query_separation_arm" not in inspect.signature(runner.run_answer).parameters
    source = inspect.getsource(runner.run_answer)
    assert "prepare_answer_state(state, strategy_override=strategy_override)" in source
    assert "query_separation_arm=" not in source


def test_original_only_does_not_import_rewriter_or_anthropic(monkeypatch, tmp_path):
    sys.modules.pop("app.retriever.legal_query_rewriter", None)
    sys.modules.pop("anthropic", None)
    expected = tmp_path / "sealed.jsonl"
    monkeypatch.setattr(
        retrieval_runner,
        "_retrieve_rows_capture",
        lambda *_a, **_k: expected,
    )
    assert (
        retrieval_runner.retrieve_rows(
            [{"id": "eval_x", "question": "q", "split": "dev"}],
            tag="original",
        )
        == expected
    )
    assert "app.retriever.legal_query_rewriter" not in sys.modules
    assert "anthropic" not in sys.modules


@pytest.mark.parametrize(
    "query_separation_arm",
    ["original_only", "original_plus_rewrite"],
)
def test_eval_capture_rejects_packaging_for_every_arm_before_artifact_retrieval_or_model(
    monkeypatch, tmp_path, query_separation_arm
):
    sys.modules.pop("app.retriever.legal_query_rewriter", None)
    sys.modules.pop("anthropic", None)
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path / "results"))
    monkeypatch.setattr(
        settings,
        "legal_query_rewrite_cache_dir",
        str(tmp_path / "rewrite-cache"),
    )
    policy = resolve_policy().policy
    policy = replace(
        policy,
        retrieval_defaults=replace(
            policy.retrieval_defaults,
            subquery_packaging_enabled=True,
        ),
    )
    monkeypatch.setattr(
        retrieval_runner,
        "resolve_policy",
        lambda: SimpleNamespace(policy=policy),
    )
    monkeypatch.setattr(
        retrieval_runner,
        "_retrieve_rows_capture",
        lambda *_a, **_k: pytest.fail("retrieval/artifact capture started"),
    )

    with pytest.raises(
        ValueError,
        match="schema 1.1 eval-retrieve requires subquery packaging to be disabled",
    ):
        retrieval_runner.retrieve_rows(
            [{"id": "eval_x", "question": "q", "split": "dev"}],
            tag="packaging-forbidden",
            query_separation_arm=query_separation_arm,
        )

    assert "app.retriever.legal_query_rewriter" not in sys.modules
    assert "anthropic" not in sys.modules
    assert not (tmp_path / "results").exists()
    assert not (tmp_path / "rewrite-cache").exists()


@pytest.mark.parametrize(
    ("eval_id", "strategy_name"),
    [
        ("eval_034", "default"),
        ("eval_053", "default"),
        ("strategy_current_law", "current_law"),
    ],
)
def test_accepted_rewrite_reaches_default_and_current_law_strategies(
    monkeypatch, eval_id, strategy_name
):
    source = f"question for {eval_id}"
    legal = source + " | Legal terms: legal rendering"
    captured = {}

    class FakeStrategy:
        def execute(self, question, knobs=None, *, legal_query=None):
            captured.update(
                question=question,
                knobs=knobs,
                legal_query=legal_query,
            )
            return SelectionResult([], [], [])

    state = AnswerState(source, debug_enabled=False)
    state.effective_question = source
    state.strategy_name = strategy_name
    state.strategy_knobs = _knobs()
    state.legal_rewrite_decision = _accepted(source, legal)
    monkeypatch.setitem(stages.STRATEGIES, strategy_name, FakeStrategy())
    stages.retrieve_context(state)
    assert captured == {
        "question": source,
        "knobs": state.strategy_knobs,
        "legal_query": legal,
    }


@pytest.mark.parametrize("strategy_name", ["default", "current_law"])
def test_real_strategy_presets_forward_the_accepted_rewrite(
    monkeypatch, strategy_name
):
    from app.retriever import strategy

    captured = {}

    def fake_select(question, knobs=None, *, legal_query=None):
        captured.update(question=question, knobs=knobs, legal_query=legal_query)
        return SelectionResult([], [], [])

    monkeypatch.setattr(context_selection, "select_context", fake_select)
    strategy.STRATEGIES[strategy_name].execute(
        "source",
        legal_query="source | Legal terms: rendering",
    )
    assert captured["question"] == "source"
    assert captured["legal_query"] == "source | Legal terms: rendering"
    assert isinstance(captured["knobs"], RetrievalKnobs)


def test_legal_rewrite_preparation_order_is_after_history_intent_and_plan(monkeypatch):
    order = []
    state = AnswerState("raw", debug_enabled=False, policy=resolve_policy().policy)

    def history(current):
        order.append("history")
        current.effective_question = "history rewritten"

    def intent(current, strategy_override=None):
        order.append("intent")

    def plan(current):
        order.append("plan")
        current.strategy_knobs = _knobs()

    def legal(current):
        order.append(("legal", current.effective_question))

    monkeypatch.setattr(stages, "rewrite_query", history)
    monkeypatch.setattr(stages, "classify_intent", intent)
    monkeypatch.setattr(stages, "plan_retrieval", plan)
    monkeypatch.setattr(stages, "prepare_legal_query_separation", legal)
    monkeypatch.setattr(stages, "retrieve_context", lambda _state: order.append("retrieve"))
    monkeypatch.setattr(stages, "gate_evidence", lambda _state: order.append("evidence"))
    runner.prepare_answer_state(
        state,
        query_separation_arm="original_plus_rewrite",
    )
    assert order == [
        "history",
        "intent",
        "plan",
        ("legal", "history rewritten"),
        "retrieve",
        "evidence",
    ]


@pytest.mark.parametrize(
    "knob_update",
    [
        {"query_decomposition_enabled": True},
        {"subquery_packaging_enabled": True},
    ],
)
def test_rewrite_arm_rejects_decomposition_or_packaging_before_import(
    monkeypatch, knob_update
):
    sys.modules.pop("app.retriever.legal_query_rewriter", None)
    state = AnswerState("q", debug_enabled=False)
    state.query_separation_arm = "original_plus_rewrite"
    state.strategy_knobs = _knobs(**knob_update)
    with pytest.raises(ValueError, match="requires"):
        stages.prepare_legal_query_separation(state)
    assert "app.retriever.legal_query_rewriter" not in sys.modules


def test_two_lanes_use_dense_sparse_identical_knobs_rrf_dedup_and_one_rerank(
    monkeypatch,
):
    knobs = _knobs()
    calls = []
    original_dense = [_result("a", 0.9, "original"), _result("b", 0.8, "original")]
    original_sparse = [_result("b", 9.0, "original"), _result("c", 8.0, "original")]
    rewrite_dense = [_result("d", 0.9, "rewrite"), _result("a", 0.8, "rewrite")]
    rewrite_sparse = [_result("d", 9.0, "rewrite"), _result("c", 8.0, "rewrite")]

    def dense(query, *, knobs):
        calls.append(("dense", query, knobs))
        return original_dense if query == "original" else rewrite_dense

    def sparse(query, *, knobs):
        calls.append(("sparse", query, knobs))
        return original_sparse if query == "original" else rewrite_sparse

    reranks = []

    def rerank(question, results, *, knobs):
        reranks.append((question, list(results), knobs))
        capture_candidates(
            "reranked",
            results,
            query_variant="original",
            query_text=question,
            score_field="rerank_score",
        )
        return results

    monkeypatch.setattr(hybrid_module, "dense_retriever", dense)
    monkeypatch.setattr(hybrid_module, "sparse_retriever", sparse)
    monkeypatch.setattr(context_selection, "rerank", rerank)
    collector = TraceCollector(capture_candidate_stages=True)
    with trace_context(trace_id="checkpoint4", collector=collector):
        selection = context_selection.select_context(
            "original",
            knobs=knobs,
            legal_query="rewrite",
        )

    assert [(kind, query) for kind, query, _ in calls] == [
        ("dense", "original"),
        ("sparse", "original"),
        ("dense", "rewrite"),
        ("sparse", "rewrite"),
    ]
    assert all(call_knobs is knobs for _, _, call_knobs in calls)
    assert len(reranks) == 1
    assert reranks[0][0] == "original"
    assert reranks[0][2] is knobs
    assert [item.chunk_id for item in selection.retrieved] == ["a", "c", "b", "d"]
    assert len({item.chunk_id for item in selection.retrieved}) == 4

    scores = selection.retrieved[0].metadata["_retrieval_scores"]
    assert scores["original_lane_rank"] == 2
    assert scores["legal_rewrite_lane_rank"] == 2
    assert scores["cross_query_rrf_score"] == pytest.approx(2 / 61)
    assert "_retrieval_scores" not in original_dense[0].metadata
    assert "_retrieval_scores" not in rewrite_dense[1].metadata

    assert [
        (snapshot["stage"], snapshot["query_variant"])
        for snapshot in collector.candidate_stages
    ] == [
        ("dense", "original"),
        ("sparse", "original"),
        ("fused", "original"),
        ("dense", "legal_rewrite"),
        ("sparse", "legal_rewrite"),
        ("fused", "legal_rewrite"),
        ("fused", "combined"),
        ("reranked", "original"),
        ("expanded", "original"),
        ("selected", "original"),
    ]
    canonical = [
        snapshot
        for snapshot in collector.candidate_stages
        if snapshot.get("pool_role") == "pre_rerank_pool"
    ]
    assert len(canonical) == 1
    assert canonical[0]["stage"] == "fused"
    assert canonical[0]["query_variant"] == "combined"

    candidate_count, stage_counts, variants = candidate_count_metadata(
        collector.candidate_stages
    )
    assert candidate_count > len(selection.retrieved)
    assert stage_counts["fused"] == len(selection.retrieved)
    assert variants["fused"] == {"original": 3, "legal_rewrite": 3}
    assert variants["dense"] == {"original": 2, "legal_rewrite": 2}


def test_cross_lane_ties_prefer_original_and_fallback_is_non_mutating():
    original = [_result("original", 0.5, "original")]
    rewrite = [_result("rewrite", 0.5, "rewrite")]
    original_metadata = deepcopy(original[0].metadata)
    rewrite_metadata = deepcopy(rewrite[0].metadata)

    assert hybrid_module.fuse_query_lanes(original, None) is original
    assert original[0].metadata == original_metadata

    combined = hybrid_module.fuse_query_lanes(original, rewrite)
    assert [result.chunk_id for result in combined] == ["original", "rewrite"]
    assert original[0].metadata == original_metadata
    assert rewrite[0].metadata == rewrite_metadata


def test_original_only_and_fallback_selection_are_identical_without_cross_metadata(
    monkeypatch,
):
    calls = []
    expected = SelectionResult(
        retrieved=[_result("a", 0.5, "original")],
        pre_expansion=[_result("a", 0.5, "original")],
        selected=[_result("a", 0.5, "original")],
    )

    class FakeStrategy:
        def execute(self, question, knobs=None, *, legal_query=None):
            calls.append(legal_query)
            return deepcopy(expected)

    monkeypatch.setitem(stages.STRATEGIES, "default", FakeStrategy())
    original = AnswerState("q", debug_enabled=False)
    original.strategy_knobs = _knobs()
    stages.retrieve_context(original)

    fallback = AnswerState("q", debug_enabled=False)
    fallback.strategy_knobs = _knobs()
    fallback.legal_rewrite_decision = SimpleNamespace(
        status="fallback",
        legal_query=None,
    )
    stages.retrieve_context(fallback)

    assert calls == [None, None]
    assert original.selection == fallback.selection
    for result in fallback.selection.selected:
        assert "_retrieval_scores" not in result.metadata
        assert "cross_query_rrf_score" not in result.metadata


def test_frozen_rewrite_record_and_ordered_bundle_hash_exclude_latency_cache_status(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path))
    policy = resolve_policy().policy
    state = AnswerState("source", debug_enabled=False, policy=policy)
    state.effective_question = "source"
    state.query_separation_arm = "original_plus_rewrite"
    state.legal_rewrite_decision = _accepted(
        "source", "source | Legal terms: legal rendering"
    )
    state.response = {
        "answer": "terminal",
        "sources": [],
        "contexts": [],
        "context_sources": [],
        "abstained": True,
        "error": False,
    }
    trace = {
        "candidate_stages": [
            {
                "stage": "fused",
                "query_variant": "combined",
                "pool_role": "pre_rerank_pool",
                "candidates": [],
            }
        ],
        "retrieval_latency_ms": 1.0,
    }
    record = make_record(state, trace)
    record["eval_id"] = "eval_034"
    frozen = record["legal_query_separation"]
    assert frozen["decision"]["status"] == "accepted"
    semantic_hash = frozen["semantic_input_hash"]
    nonsemantic_change = deepcopy(frozen)
    nonsemantic_change["decision"]["call_latency_ms"] = 9999.0
    nonsemantic_change["decision"]["cache_status"] = "miss_written"
    assert _legal_query_separation_semantic_input_hash(nonsemantic_change) == semantic_hash

    paths = paths_for("rewrite-bundle", create=True)
    written = append_hashed_row(paths.partial, record)
    sealed_meta = seal(
        paths,
        meta={"tag": "rewrite-bundle", "holdout": False},
        rows=[written],
        targets_by_id={"eval_034": {"match_mode": "exact", "targets": []}},
    )
    expected = sha256([{"eval_id": "eval_034", "hash": semantic_hash}])
    assert sealed_meta["ordered_legal_query_separation_semantic_input_hash"] == expected
    assert read_json(paths.state)[
        "ordered_legal_query_separation_semantic_input_hash"
    ] == expected


def test_integrity_prompt_identity_matches_checkpoint3_contract():
    from app.evals import integrity
    from app.retriever import legal_query_rewriter

    identity = query_separation_identity()
    assert identity["prompt_version"] == "v3"
    assert identity["prompt_hash"] == (
        "a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91"
    )
    assert identity["prompt_hash"] == legal_query_rewriter.legal_rewrite_prompt_hash()
    assert (
        integrity.QUERY_SEPARATION_PROMPT_VERSION
        == legal_query_rewriter.LEGAL_REWRITE_PROMPT_VERSION
    )
    assert (
        integrity._QUERY_SEPARATION_SYSTEM_PROMPT
        == legal_query_rewriter.LEGAL_REWRITE_SYSTEM_PROMPT
    )
    assert (
        integrity._QUERY_SEPARATION_PROMPT_TEMPLATE
        == legal_query_rewriter._PROMPT_TEMPLATE
    )
    assert (
        integrity._QUERY_SEPARATION_ASSISTANT_PREFILL
        == legal_query_rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL
    )
    assert (
        integrity._QUERY_SEPARATION_RESPONSE_RECONSTRUCTION
        == legal_query_rewriter.LEGAL_REWRITE_RESPONSE_RECONSTRUCTION
    )


def test_holdout_rejected_before_capture_lock_artifact_or_rewriter(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "legal_query_rewrite_cache_dir", str(tmp_path / "cache"))
    sys.modules.pop("app.retriever.legal_query_rewriter", None)
    monkeypatch.setattr(
        retrieval_runner,
        "_retrieve_rows_capture",
        lambda *_a, **_k: pytest.fail("capture started for holdout"),
    )
    monkeypatch.setattr(
        retrieval_runner,
        "resolve_policy",
        lambda: pytest.fail("policy resolved for holdout"),
    )
    with pytest.raises(ValueError, match="holdout"):
        retrieval_runner.retrieve_rows(
            [{"id": "sealed", "question": "q", "split": "holdout"}],
            tag="forbidden",
            query_separation_arm="original_plus_rewrite",
        )
    assert "app.retriever.legal_query_rewriter" not in sys.modules
    assert not (tmp_path / "results").exists()
    assert not (tmp_path / "cache").exists()


def test_rewrite_capture_lock_wraps_artifact_work_and_releases_in_finally(
    monkeypatch, tmp_path
):
    legal_query_rewriter = importlib.import_module(
        "app.retriever.legal_query_rewriter"
    )

    events = []

    @contextmanager
    def lock():
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def capture(*_args, **_kwargs):
        assert events == ["lock-enter"]
        (tmp_path / "artifact").write_text("created", encoding="utf-8")
        events.append("artifact-created")
        raise RuntimeError("capture failed")

    monkeypatch.setattr(legal_query_rewriter, "legal_rewrite_capture", lock)
    monkeypatch.setattr(retrieval_runner, "_retrieve_rows_capture", capture)
    with pytest.raises(RuntimeError, match="capture failed"):
        retrieval_runner.retrieve_rows(
            [{"id": "eval_x", "question": "q", "split": "dev"}],
            tag="rewrite",
            query_separation_arm="original_plus_rewrite",
        )
    assert events == ["lock-enter", "artifact-created", "lock-exit"]


def test_capture_lock_contention_precedes_eval_artifact_and_model_access(
    monkeypatch, tmp_path
):
    legal_query_rewriter = importlib.import_module(
        "app.retriever.legal_query_rewriter"
    )
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path / "results"))
    monkeypatch.setattr(
        settings,
        "legal_query_rewrite_cache_dir",
        str(tmp_path / "rewrite-cache"),
    )
    monkeypatch.setattr(
        legal_query_rewriter.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )
    monkeypatch.setattr(
        legal_query_rewriter,
        "_call_haiku",
        lambda _prompt: pytest.fail("model call attempted under lock contention"),
    )
    monkeypatch.setattr(
        retrieval_runner,
        "_retrieve_rows_capture",
        lambda *_a, **_k: pytest.fail("eval artifact capture started under contention"),
    )
    with pytest.raises(legal_query_rewriter.LegalRewriteCaptureBusy):
        retrieval_runner.retrieve_rows(
            [{"id": "eval_x", "question": "q", "split": "dev"}],
            tag="contended",
            query_separation_arm="original_plus_rewrite",
        )
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize(
    ("cache_state", "expected_status"),
    [("cached", "hit"), ("pending", "pending_recovered")],
)
def test_resume_uses_cached_or_pending_decision_without_model_call(
    monkeypatch, tmp_path, cache_state, expected_status
):
    legal_query_rewriter = importlib.import_module(
        "app.retriever.legal_query_rewriter"
    )
    monkeypatch.setattr(settings, "eval_results_dir", str(tmp_path / "results"))
    monkeypatch.setattr(
        settings,
        "legal_query_rewrite_cache_dir",
        str(tmp_path / "rewrite-cache"),
    )
    rows = [
        {"id": "eval_034", "question": "first", "split": "dev"},
        {"id": "eval_053", "question": "second", "split": "dev"},
    ]
    monkeypatch.setattr(
        "app.evals.retrieval_targets.load_retrieval_targets",
        lambda: {
            row["id"]: {"eval_id": row["id"], "match_mode": "exact", "targets": []}
            for row in rows
        },
    )
    storage = {
        "corpus_identity": {"hash": "corpus"},
        "bm25_identity": {"hash": "bm25"},
        "qdrant_identity": {"hash": "qdrant"},
        "index_identity": {"hash": "index"},
    }
    monkeypatch.setattr(retrieval_runner, "_storage_identities", lambda: storage)
    monkeypatch.setattr(
        "app.retriever.reranker.release_retrieval_models",
        lambda: {"attempted": True, "warning": None},
    )

    source = rows[1]["question"]
    key = legal_query_rewriter.legal_rewrite_cache_key(source)
    cache_dir = legal_query_rewriter._cache_dir()
    cache_dir.mkdir(parents=True)
    if cache_state == "cached":
        raw = json.dumps(
            {
                "legal_query": source + " | Legal terms: legal rendering",
                "citations": [],
                "confidence": "high",
            },
            separators=(",", ":"),
        )
        decision = legal_query_rewriter._decision_from_parse(
            source,
            legal_query_rewriter.parse_legal_rewrite_output(source, raw),
            raw_output_hash=legal_query_rewriter._sha256(raw),
            call_latency_ms=1.0,
            cache_key=key,
        )
        legal_query_rewriter._atomic_write_json(
            cache_dir / f"{key}.json",
            legal_query_rewriter.asdict(decision),
        )
    else:
        (cache_dir / f"{key}.pending.json").write_text(
            json.dumps({"cache_key": key, "state": "pending"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        legal_query_rewriter,
        "_call_haiku",
        lambda _prompt: pytest.fail("model call attempted during resume"),
    )

    phase = {"resuming": False}

    def fake_prepare(state, *, query_separation_arm):
        state.query_separation_arm = query_separation_arm
        if state.question == source and not phase["resuming"]:
            raise RuntimeError("simulated interruption")
        if state.question == source:
            state.legal_rewrite_decision = legal_query_rewriter.rewrite_legal_query(
                source
            )
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
            "answer": "terminal",
            "sources": [],
            "contexts": [],
            "context_sources": [],
            "abstained": True,
            "error": False,
        }

    monkeypatch.setattr(retrieval_runner, "prepare_answer_state", fake_prepare)
    tag = f"resume-{cache_state}"
    with pytest.raises(RuntimeError, match="simulated interruption"):
        retrieval_runner.retrieve_rows(
            rows,
            tag=tag,
            query_separation_arm="original_plus_rewrite",
        )
    phase["resuming"] = True
    sealed = retrieval_runner.retrieve_rows(
        rows,
        tag=tag,
        resume=True,
        query_separation_arm="original_plus_rewrite",
    )
    frozen = read_hashed_rows(sealed)
    assert [row["eval_id"] for row in frozen] == ["eval_034", "eval_053"]
    assert frozen[1]["legal_query_separation"]["decision"]["cache_status"] == (
        expected_status
    )


def test_generation_replay_never_imports_rewriter_or_anthropic():
    code = (
        "import sys; import app.evals.generation_replay; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    probe = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    loaded = set(probe.stdout.splitlines())
    assert "anthropic" not in loaded
    assert "app.retriever.legal_query_rewriter" not in loaded
