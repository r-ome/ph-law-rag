from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.types import RetrievalResult


QUESTION = "What is the prescriptive period for a verbal loan under the Civil Code?"
TARGETS = [{"source_id": "civil_code", "provision_id": "civil_code:article:1145"}]


def _clone(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        RetrievalResult(result.chunk_id, result.text, result.score, dict(result.metadata))
        for result in results
    ]


def _matches(result: RetrievalResult) -> bool:
    return any(
        result.metadata.get("source_id") == target["source_id"]
        and result.metadata.get("provision_id") == target["provision_id"]
        for target in TARGETS
    )


def _rank(results: list[RetrievalResult]) -> dict:
    for i, result in enumerate(results, start=1):
        if _matches(result):
            return {
                "present": True,
                "rank": i,
                "chunk_id": result.chunk_id,
                "score": result.score,
                "unit_label": result.metadata.get("unit_label"),
                "preview": result.text[:180],
            }
    return {"present": False}


def _full_rerank(question: str, candidates: list[RetrievalResult]) -> list[RetrievalResult]:
    original_top_n = settings.rerank_top_n
    original_margin = settings.rerank_score_margin
    try:
        settings.rerank_top_n = max(len(candidates), original_top_n)
        if settings.reranker_backend == "minilm":
            settings.rerank_score_margin = 1_000_000.0
        return rerank(question, _clone(candidates))
    finally:
        settings.rerank_top_n = original_top_n
        settings.rerank_score_margin = original_margin


def _classify(candidate_hit: dict, selected_hit: dict, full_hit: dict) -> str:
    if not candidate_hit["present"]:
        return "pool_miss"
    if selected_hit["present"]:
        return "selected"
    if full_hit["present"] and full_hit["rank"] and full_hit["rank"] > settings.rerank_top_n:
        return "rerank_top_n_sensitivity"
    return "selector_cut"


def main() -> None:
    candidates = hybrid_retriever(QUESTION)
    selected = rerank(QUESTION, _clone(candidates))
    full = _full_rerank(QUESTION, candidates)
    output = {
        "question": QUESTION,
        "targets": TARGETS,
        "reranker_backend": settings.reranker_backend,
        "dense_top_k": settings.dense_top_k,
        "rerank_top_n": settings.rerank_top_n,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "candidate_hit": _rank(candidates),
        "selected_hit": _rank(selected),
        "full_rerank_hit": _rank(full),
    }
    output["classification"] = _classify(
        output["candidate_hit"],
        output["selected_hit"],
        output["full_rerank_hit"],
    )

    out_path = Path(settings.eval_results_dir) / "trace_art1145.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
