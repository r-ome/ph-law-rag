import pytest

from app.config import settings
from app.retriever import context_selection, edge_expansion, strategy, subquery_retrieval
from app.retriever.context_selection import SelectionResult
from app.retriever.strategy import RetrievalKnobs, resolve_knobs
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _knobs(**overrides) -> RetrievalKnobs:
    values = {
        "dense_top_k": 3,
        "sparse_top_k": 4,
        "rerank_top_n": 2,
        "parent_expansion_enabled": True,
        "prefer_operative_enabled": False,
        "retrieval_operative_only": True,
        "consolidated_dedup_enabled": True,
    }
    values.update(overrides)
    return RetrievalKnobs(**values)


def _r(chunk_id: str, **metadata) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id, text=chunk_id, score=1.0, metadata=metadata)


def test_default_resolves_to_current_effective_settings(monkeypatch):
    monkeypatch.setattr(settings, "dense_top_k", 31)
    monkeypatch.setattr(settings, "sparse_top_k", 11)
    monkeypatch.setattr(settings, "rerank_top_n", 7)
    monkeypatch.setattr(settings, "parent_expansion_enabled", False)
    monkeypatch.setattr(settings, "prefer_operative_enabled", True)
    monkeypatch.setattr(settings, "retrieval_operative_only", False)
    monkeypatch.setattr(settings, "consolidated_dedup_enabled", False)

    assert resolve_knobs("default") == RetrievalKnobs(
        dense_top_k=31,
        sparse_top_k=11,
        rerank_top_n=7,
        parent_expansion_enabled=False,
        prefer_operative_enabled=True,
        retrieval_operative_only=False,
        consolidated_dedup_enabled=False,
    )


def test_pinned_preset_beats_settings(monkeypatch):
    pinned = _knobs(dense_top_k=5, sparse_top_k=6, rerank_top_n=1)
    monkeypatch.setattr(settings, "dense_top_k", 999)
    monkeypatch.setattr(settings, "sparse_top_k", 999)
    monkeypatch.setattr(settings, "rerank_top_n", 999)
    monkeypatch.setitem(strategy._PRESET_KNOBS, "pinned", pinned)

    assert resolve_knobs("pinned") == pinned


def test_current_law_registered_from_r3_trace():
    knobs = resolve_knobs("current_law")

    assert "current_law" in strategy.STRATEGIES
    assert knobs == RetrievalKnobs(
        dense_top_k=30,
        sparse_top_k=10,
        rerank_top_n=8,
        parent_expansion_enabled=True,
        prefer_operative_enabled=True,
        retrieval_operative_only=True,
        consolidated_dedup_enabled=True,
    )


def test_current_law_preset_does_not_read_behavior_settings(monkeypatch):
    monkeypatch.setattr(settings, "sparse_overfetch_k", 999)
    monkeypatch.setattr(settings, "rerank_score_margin", 99.0)
    monkeypatch.setattr(settings, "max_distance", 0.99)
    monkeypatch.setattr(settings, "edge_expansion_enabled", False)
    monkeypatch.setattr(settings, "edge_hop_top_k", 99)
    monkeypatch.setattr(settings, "parent_expansion_min_children", 99)
    monkeypatch.setattr(settings, "parent_expansion_max_chars", 99)
    monkeypatch.setattr(settings, "query_planner_model", "other-model")
    monkeypatch.setattr(settings, "query_planner_max_subqueries", 99)
    monkeypatch.setattr(settings, "subquery_packaging_enabled", True)
    monkeypatch.setattr(settings, "subquery_reserve_n", 99)

    knobs = resolve_knobs("current_law")

    assert knobs.sparse_overfetch_k == 100
    assert knobs.rerank_score_margin == 6.0
    assert knobs.max_distance == 0.5
    assert knobs.edge_expansion_enabled is True
    assert knobs.edge_hop_top_k == 3
    assert knobs.parent_expansion_min_children == 2
    assert knobs.parent_expansion_max_chars == 8000
    assert knobs.query_planner_model == "mistral"
    assert knobs.query_planner_max_subqueries == 3
    assert knobs.subquery_packaging_enabled is False
    assert knobs.subquery_reserve_n == 2


def test_r3_candidate_stubs_cleared_after_decisions():
    assert strategy.CANDIDATE_PRESET_STUBS == ()
    assert "citation_precision" not in strategy._PRESET_KNOBS
    assert "citation_precision" not in strategy.STRATEGIES


def test_default_strategy_passes_resolved_knobs(monkeypatch):
    captured = {}
    expected = _knobs(dense_top_k=12)
    monkeypatch.setitem(strategy._PRESET_KNOBS, "default", expected)

    def fake_select_context(question, knobs=None):
        captured["question"] = question
        captured["knobs"] = knobs
        return SelectionResult(retrieved=[], pre_expansion=[], selected=[])

    monkeypatch.setattr("app.retriever.context_selection.select_context", fake_select_context)

    selection = strategy.STRATEGIES["default"].execute("standalone question")

    assert selection == SelectionResult(retrieved=[], pre_expansion=[], selected=[])
    assert captured == {"question": "standalone question", "knobs": expected}


def test_pinned_preset_flows_through_real_select_context(monkeypatch):
    pinned = _knobs(rerank_top_n=1)
    captured = {}

    monkeypatch.setitem(strategy._PRESET_KNOBS, "default", pinned)
    monkeypatch.setattr(settings, "dense_top_k", 999)
    monkeypatch.setattr(settings, "sparse_top_k", 999)
    monkeypatch.setattr(settings, "rerank_top_n", 999)
    monkeypatch.setattr(settings, "subquery_packaging_enabled", False)
    monkeypatch.setattr(settings, "edge_expansion_enabled", True)
    monkeypatch.setattr(settings, "prefer_operative_enabled", False)
    monkeypatch.setattr(settings, "parent_expansion_enabled", False)
    monkeypatch.setattr(settings, "consolidated_dedup_enabled", False)

    def fake_hybrid(question, knobs=None):
        captured["hybrid"] = knobs
        return [_r("raw")]

    def fake_rerank(question, results, knobs=None):
        captured["rerank"] = knobs
        return [_r("reranked")]

    def fake_expand_with_edges(question, seed, knobs=None):
        captured["edge"] = knobs
        return seed

    monkeypatch.setattr(context_selection, "hybrid_retriever", fake_hybrid)
    monkeypatch.setattr(context_selection, "rerank", fake_rerank)
    monkeypatch.setattr(context_selection, "expand_with_edges", fake_expand_with_edges)

    selection = strategy.STRATEGIES["default"].execute("question")

    assert selection.pre_expansion == [_r("reranked")]
    assert captured == {"hybrid": pinned, "rerank": pinned, "edge": pinned}


def test_trace_carries_resolved_strategy_block(monkeypatch):
    from app.retriever import answer_service
    from app.pipeline import runner

    records = []

    class FakeTraceWriter:
        def write(self, record):
            records.append(record)

    monkeypatch.setattr(settings, "trace_logging_enabled", True)
    monkeypatch.setattr(settings, "dense_top_k", 33)
    monkeypatch.setattr(runner, "TraceWriter", lambda: FakeTraceWriter())

    answer_service.answer("hi", trace=True)

    assert len(records) == 1
    block = records[0]["retrieval_strategy"]
    assert block["strategy"] == "default"
    assert block["knobs"]["dense_top_k"] == 33
    assert "dense_top_k" not in records[0]["feature_flags"]
    assert "retrieval_operative_only" not in records[0]["feature_flags"]


def test_edge_expansion_uses_resolved_rerank_top_n(monkeypatch):
    knobs = _knobs(rerank_top_n=1)
    captured = {}
    seed = [_r("seed", source_id="seed", title="Seed")]
    extra = [_r("extra", source_id="neighbor")]

    monkeypatch.setattr(edge_expansion, "neighbors", lambda source_id: {"neighbor": "amends"})
    monkeypatch.setattr(edge_expansion, "dense_retriever", lambda *args, **kwargs: extra)

    def fake_rerank(question, results, knobs=None):
        captured["knobs"] = knobs
        return results[:knobs.rerank_top_n]

    monkeypatch.setattr(edge_expansion, "rerank", fake_rerank)

    out = edge_expansion.expand_with_edges("question", seed, knobs=knobs)

    assert captured["knobs"] == knobs
    assert [r.chunk_id for r in out] == ["seed"]


def test_packaged_retrieve_caps_with_resolved_rerank_top_n(monkeypatch):
    knobs = _knobs(rerank_top_n=1)
    monkeypatch.setattr(settings, "subquery_reserve_n", 2)
    monkeypatch.setattr(
        subquery_retrieval,
        "_plan",
        lambda question, model=None, max_subqueries=None: ["first", "second"],
    )
    monkeypatch.setattr(
        subquery_retrieval,
        "dense_retriever",
        lambda query, knobs=None: [_r(f"dense-{query}")],
    )
    monkeypatch.setattr(
        subquery_retrieval,
        "sparse_retriever",
        lambda query, knobs=None: [_r(f"sparse-{query}")],
    )
    monkeypatch.setattr(
        subquery_retrieval,
        "rerank",
        lambda query, results, knobs=None: results,
    )

    out = subquery_retrieval.packaged_retrieve("question", knobs=knobs)

    assert len(out) == 1


def test_round_robin_merge_skips_seen_ids_and_preserves_rank_order():
    out = subquery_retrieval.round_robin_merge(
        [
            [_r("a1"), _r("a2")],
            [_r("seen"), _r("b2")],
            [_r("c1")],
        ],
        seen_ids={"seen"},
        cap=3,
    )

    assert [result.chunk_id for result in out] == ["a1", "c1", "a2"]
