"""Free, local diagnostic for the answerability gate.

Runs retrieval/rerank/edge-expansion once per question and calls only
is_answerable() — no answer() generation, no RAGAS judge tokens. Use this to
tune the gate prompt against the eval set before cutting a new RAGAS baseline.

Needs live Qdrant + Ollama + BM25. Run directly (not via pytest):
    uv run python scripts/check_gate.py
"""
import json

from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.answerability import is_answerable
from app.config import settings


def _ctx(question: str):
    reranked = rerank(question, hybrid_retriever(question))
    if settings.edge_expansion_enabled:
        reranked = expand_with_edges(question, reranked)
    return reranked


def check() -> None:
    # This measures ONLY the LLM answerability gate on retrieved contexts,
    # not the full abstention pipeline (no min-chunks pre-filter applied).
    rows = [json.loads(line) for line in open(settings.eval_dataset_path)]
    oos = [r for r in rows if r["category"] == "out-of-scope"]
    factual = [r for r in rows if r["category"] == "factual"][:10]

    oos_caught = sum(not is_answerable(r["question"], _ctx(r["question"])) for r in oos)
    fact_kept = sum(is_answerable(r["question"], _ctx(r["question"])) for r in factual)

    print(f"OOS caught (want high):              {oos_caught}/{len(oos)}")
    print(f"Factual kept (want {len(factual)}/{len(factual)}, no regression): {fact_kept}/{len(factual)}")


if __name__ == "__main__":
    check()
