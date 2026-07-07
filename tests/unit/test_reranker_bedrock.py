import pytest

from app.config import settings
from app.retriever import reranker
from app.retriever.types import RetrievalResult

pytestmark = pytest.mark.unit


def _r(chunk_id: str, text: str) -> RetrievalResult:
	return RetrievalResult(chunk_id=chunk_id, text=text, score=0.0, metadata={})


class _FakeBedrockClient:
	def __init__(self, results: list[dict]):
		self.results = results
		self.calls: list[dict] = []

	def rerank(self, **kwargs):
		self.calls.append(kwargs)
		return {"results": self.results}


@pytest.fixture(autouse=True)
def _bedrock_test_state(monkeypatch):
	monkeypatch.setattr(settings, "reranker_backend", "bedrock")
	# fresh pacing state per test; no real sleeping
	monkeypatch.setattr(reranker, "_bedrock_last_call", 0.0)
	monkeypatch.setattr(reranker.time, "sleep", lambda s: pytest.fail("unexpected sleep"))


def _install_client(monkeypatch, results: list[dict]) -> _FakeBedrockClient:
	client = _FakeBedrockClient(results)
	monkeypatch.setattr(reranker, "_get_bedrock_client", lambda: client)
	return client


def test_bedrock_request_shape_scores_every_candidate(monkeypatch):
	client = _install_client(monkeypatch, [
		{"index": 0, "relevanceScore": 0.5},
		{"index": 1, "relevanceScore": 0.1},
	])

	reranker._bedrock_scores("what is a felony", ["doc a", "doc b"])

	assert len(client.calls) == 1
	call = client.calls[0]
	assert call["queries"] == [{"type": "TEXT", "textQuery": {"text": "what is a felony"}}]
	assert [s["inlineDocumentSource"]["textDocument"]["text"] for s in call["sources"]] == ["doc a", "doc b"]
	config = call["rerankingConfiguration"]["bedrockRerankingConfiguration"]
	assert config["numberOfResults"] == 2  # a score for every candidate, not just top-n
	assert config["modelConfiguration"]["modelArn"] == (
		f"arn:aws:bedrock:{settings.bedrock_rerank_region}"
		f"::foundation-model/{settings.bedrock_rerank_model}"
	)


def test_bedrock_scores_map_back_by_index_not_response_order(monkeypatch):
	# The API returns results sorted by relevance; index points at the input position.
	_install_client(monkeypatch, [
		{"index": 2, "relevanceScore": 0.9},
		{"index": 0, "relevanceScore": 0.4},
		{"index": 1, "relevanceScore": 0.02},
	])

	scores = reranker._bedrock_scores("q", ["a", "b", "c"])

	assert scores == [0.4, 0.02, 0.9]


def test_rerank_bedrock_sorts_and_takes_plain_top_n_without_margin(monkeypatch):
	monkeypatch.setattr(settings, "rerank_top_n", 2)
	# Margin must not apply to bedrock: an uncalibrated [0,1]-ish spread would keep
	# the whole pool under the MiniLM-calibrated margin of 6.0.
	monkeypatch.setattr(settings, "rerank_score_margin", 6.0)
	_install_client(monkeypatch, [
		{"index": 1, "relevanceScore": 0.7},
		{"index": 2, "relevanceScore": 0.3},
		{"index": 0, "relevanceScore": 0.001},
	])

	kept = reranker.rerank("q", [_r("c0", "a"), _r("c1", "b"), _r("c2", "c")])

	assert [r.chunk_id for r in kept] == ["c1", "c2"]
	assert [r.score for r in kept] == [0.7, 0.3]


def test_bedrock_paces_calls_to_respect_per_minute_quota(monkeypatch):
	_install_client(monkeypatch, [{"index": 0, "relevanceScore": 0.5}])
	sleeps: list[float] = []
	monkeypatch.setattr(reranker.time, "sleep", sleeps.append)
	monkeypatch.setattr(reranker.time, "monotonic", lambda: 1000.0)

	# a call 10s ago must wait out the rest of the 31s window
	monkeypatch.setattr(reranker, "_bedrock_last_call", 990.0)
	reranker._bedrock_scores("q", ["a"])
	assert sleeps == [pytest.approx(21.0)]

	# a call outside the window proceeds immediately
	sleeps.clear()
	monkeypatch.setattr(reranker, "_bedrock_last_call", 900.0)
	reranker._bedrock_scores("q", ["a"])
	assert sleeps == []
