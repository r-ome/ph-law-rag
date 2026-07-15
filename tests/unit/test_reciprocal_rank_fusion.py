import pytest

from app.retriever import hybrid_retriever as hr
from app.retriever.types import RetrievalResult
from app.retriever.hybrid_retriever import _fuse, hybrid_retriever, RRF_K

def _result(chunk_id: str) -> RetrievalResult:
    return RetrievalResult(chunk_id=chunk_id,text=f"text-{chunk_id}", score=0.0, metadata={})

def _patch(monkeypatch, dense, sparse):
    monkeypatch.setattr(hr, "dense_retriever", lambda _q, knobs=None: dense)
    monkeypatch.setattr(hr, "sparse_retriever", lambda _q, knobs=None: sparse)
    
pytestmark = pytest.mark.unit

def test_doc_in_both_lists_outranks_docs_in_one(monkeypatch):
    a, b, c = _result("A"), _result("B"), _result("C")
    _patch(monkeypatch=monkeypatch, dense=[a,b], sparse=[c,a])
    fused = hybrid_retriever("q")
    assert fused[0].chunk_id == "A"
    
def test_score_is_reciprocal_rank_sum(monkeypatch):
    a, b, c = _result("A"), _result("B"), _result("C")
    _patch(monkeypatch=monkeypatch, dense=[a,b], sparse=[c,a])
    
    fused = hybrid_retriever("q")
    by_id = { r.chunk_id: r for r in fused}
    
    assert by_id["A"].score == pytest.approx(1 / RRF_K +1 /(RRF_K+1))
    assert by_id["B"].score == pytest.approx(1 /(RRF_K+1))
    assert by_id["C"].score == pytest.approx(1 / RRF_K)
    
def test_each_chunk_appears_once(monkeypatch):
    a,b = _result("A"), _result("B")
    _patch(monkeypatch=monkeypatch, dense=[a,b], sparse=[b,a])
    fused = hybrid_retriever("q")
    
    assert [r.chunk_id for r in fused].count("A") == 1
    assert len(fused) == 2
    
def test_results_sorted_by_score_descending(monkeypatch):
    a, b, c = _result("A"), _result("B"), _result("C")
    _patch(monkeypatch=monkeypatch, dense=[a,b], sparse=[c,a])
    fused = hybrid_retriever("q")
    scores = [r.score for r in fused]
    assert scores == sorted(scores, reverse=True)

def test_empty_inputs_return_empty(monkeypatch):
    _patch(monkeypatch=monkeypatch, dense=[], sparse=[])
    assert hybrid_retriever("q") == []


def test_fusion_clones_results_without_overwriting_arm_scores():
    dense_a = RetrievalResult("A", "dense-a", 0.91, {"arm": "dense"})
    dense_b = RetrievalResult("B", "dense-b", 0.72, {})
    sparse_a = RetrievalResult("A", "sparse-a", 7.5, {"arm": "sparse"})

    fused = _fuse(
        [[dense_a, dense_b], [sparse_a]],
        score_fields=["dense_score", "sparse_score"],
    )

    assert [result.chunk_id for result in fused] == ["A", "B"]
    assert dense_a.score == 0.91
    assert dense_b.score == 0.72
    assert sparse_a.score == 7.5
    assert dense_a.metadata == {"arm": "dense"}
    assert fused[0] is not dense_a
    assert fused[0].metadata["_retrieval_scores"] == {
        "dense_score": 0.91,
        "sparse_score": 7.5,
        "fused_score": pytest.approx(2 / RRF_K),
    }
