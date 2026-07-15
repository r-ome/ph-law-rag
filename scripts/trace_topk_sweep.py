from __future__ import annotations

# Provenance note: this script was created during selector exploration. It now
# calls production rerank(), but some setup assumptions predate the default
# k30+Qwen backend. Use trace_art1145.py for the focused Art. 1145 diagnosis.

import json
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.indexing.embedder import get_embed_model
from app.indexing.index_service import get_qdrant_client
from app.indexing.vector_store import operative_filter, query
from app.retriever.dense_retriever import _format_embedding_query
from app.retriever.hybrid_retriever import _fuse
from app.retriever.reranker import _get_bedrock_client, _get_model, _get_qwen, rerank
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.types import RetrievalResult


TOP_K_VALUES = [10, 15, 20, 25, 30, 40]

WORKSET = [
    {
        "key": "felony_art3",
        "question": "How does the Revised Penal Code define a felony?",
        "targets": [{"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:3"}],
    },
    {
        "key": "sale_perfection_art1475",
        "question": "When I buy something, at what point is the sale actually a done deal?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1475"}],
    },
    {
        "key": "drug_possession_sec11",
        "question": "What can happen to me if I'm caught holding illegal drugs?",
        "targets": [{"source_id": "dangerous_drugs_act", "provision_id": "dangerous_drugs_act:article-ii:section:11"}],
    },
    {
        "key": "online_libel_sec6",
        "question": "Is libel posted online treated the same as ordinary libel, and is the penalty the same?",
        "targets": [{"source_id": "cybercrime_prevention_act", "provision_id": "cybercrime_prevention_act:chapter-ii:section:6"}],
    },
    {
        "key": "verbal_land_sale_art1403",
        "question": "Is a purely verbal agreement to sell a parcel of land valid in the Philippines?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1403"}],
    },
    {
        "key": "rtc_jurisdiction_sec19",
        "question": "What civil cases fall under the exclusive original jurisdiction of the Regional Trial Courts?",
        "targets": [
            {"source_id": "judiciary_reorganization_act", "provision_id": "judiciary_reorganization_act:chapter-ii:section:19"},
            {"source_id": "judiciary_reorganization_act", "provision_id": "judiciary_reorganization_act:section:19"},
            {"source_id": "judiciary_reorganization_amendments_2021", "provision_id": "judiciary_reorganization_act:section:19"},
        ],
        "instrumentation_only": True,
    },
    {
        "key": "obligation_from_crime_bridge",
        "question": "Can an obligation to pay arise from a crime, and where is that recognized in the Civil Code?",
        "targets": [
            {"source_id": "civil_code", "provision_id": "civil_code:article:1157"},
            {"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:100"},
        ],
        "instrumentation_only": True,
    },
]


@dataclass
class StageHit:
    present: bool
    rank: int | None = None
    chunk_id: str | None = None
    source_id: str | None = None
    provision_id: str | None = None
    unit_label: str | None = None
    score: float | None = None
    distance: float | None = None


def _load_eval_questions() -> set[str]:
    path = Path(settings.eval_dataset_path)
    return {
        json.loads(line)["question"]
        for line in path.read_text().splitlines()
        if line.strip()
    }


def _matches(result: RetrievalResult, targets: list[dict[str, str]]) -> bool:
    source_id = result.metadata.get("source_id")
    provision_id = result.metadata.get("provision_id")
    for target in targets:
        if source_id != target["source_id"]:
            continue
        if provision_id == target["provision_id"]:
            return True
    return False


def _first_hit(
    results: list[RetrievalResult],
    targets: list[dict[str, str]],
    *,
    dense_distance: bool = False,
) -> StageHit:
    for rank, result in enumerate(results, start=1):
        if _matches(result, targets):
            return StageHit(
                present=True,
                rank=rank,
                chunk_id=result.chunk_id,
                source_id=result.metadata.get("source_id"),
                provision_id=result.metadata.get("provision_id"),
                unit_label=result.metadata.get("unit_label"),
                score=result.score,
                distance=(1 - result.score) if dense_distance else None,
            )
    return StageHit(False)


def _hit_payload(
    hit: StageHit,
    *,
    score_field: str,
    include_distance: bool = False,
) -> dict:
    payload = {
        "present": hit.present,
        "rank": hit.rank,
        "chunk_id": hit.chunk_id,
        "source_id": hit.source_id,
        "provision_id": hit.provision_id,
        "unit_label": hit.unit_label,
        score_field: hit.score,
        "score_provenance": score_field,
    }
    if include_distance:
        payload["distance"] = hit.distance
        payload["distance_provenance"] = "1 - qdrant_cosine_similarity"
    return payload


def _dense_raw(query_text: str, top_k: int) -> list[RetrievalResult]:
    embed_model = get_embed_model()
    query_vector = embed_model.get_query_embedding(_format_embedding_query(query_text))
    client = get_qdrant_client()
    points = query(client, query_vector, top_k, query_filter=operative_filter(None))
    return [
        RetrievalResult(
            chunk_id=str(point.id),
            text=point.payload["text"],
            score=point.score,
            metadata={k: v for k, v in point.payload.items() if k != "text"},
        )
        for point in points
    ]


def _distance_filter(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [result for result in results if 1 - result.score <= settings.max_distance]


def _trace(question: str, targets: list[dict[str, str]], top_k: int) -> dict:
    dense_raw = _dense_raw(question, top_k)
    dense_filtered = _distance_filter(dense_raw)
    sparse = sparse_retriever(question)
    fused = _fuse([dense_filtered, sparse], score_fields=["dense_score", "sparse_score"])

    start = time.perf_counter()
    rerank_input = [RetrievalResult(r.chunk_id, r.text, r.score, dict(r.metadata)) for r in fused]
    reranked = rerank(question, rerank_input)
    rerank_ms = (time.perf_counter() - start) * 1000

    dense_raw_hit = _first_hit(dense_raw, targets, dense_distance=True)
    dense_filtered_hit = _first_hit(dense_filtered, targets, dense_distance=True)
    hybrid_hit = _first_hit(fused, targets)
    final_hit = _first_hit(reranked, targets)

    return {
        "top_k": top_k,
        "dense_raw_n": len(dense_raw),
        "dense_filtered_n": len(dense_filtered),
        "sparse_n": len(sparse),
        "hybrid_n": len(fused),
        "final_n": len(reranked),
        "rerank_ms": round(rerank_ms, 1),
        "dense_pool": _hit_payload(
            dense_raw_hit, score_field="dense_score", include_distance=True
        ),
        "distance_pass": _hit_payload(
            dense_filtered_hit, score_field="dense_score", include_distance=True
        ),
        "hybrid_candidate": _hit_payload(hybrid_hit, score_field="fused_score"),
        "final": _hit_payload(final_hit, score_field="rerank_score"),
        "provenance": {
            "embedding_query_formatter": "app.retriever.dense_retriever._format_embedding_query",
            "embedding_query_instruction": settings.embedding_query_instruction,
            "fusion": "rrf",
            "reranker_backend": settings.reranker_backend,
            "reranker_model": (
                settings.qwen3_reranker_model
                if settings.reranker_backend == "qwen3"
                else settings.bedrock_rerank_model
                if settings.reranker_backend == "bedrock"
                else settings.reranker_model
            ),
        },
    }


def main() -> None:
    eval_questions = _load_eval_questions()
    missing = [row["question"] for row in WORKSET if row["question"] not in eval_questions]
    if missing:
        raise SystemExit(f"workset question not found in eval dataset: {missing}")

    # Load the reranker before timing so the reported latency is pair scoring, not model startup.
    if settings.reranker_backend == "qwen3":
        _get_qwen()
    elif settings.reranker_backend == "bedrock":
        _get_bedrock_client()
    else:
        _get_model()

    output = []
    for row in WORKSET:
        print(f"\n## {row['key']}")
        print(row["question"])
        for top_k in TOP_K_VALUES:
            trace = _trace(row["question"], row["targets"], top_k)
            output.append({**row, "trace": trace})
            print(
                "top_k={top_k:>2} dense={dense} dist={dist} final={final} "
                "final_rank={rank} pairs={pairs} rerank_ms={ms}".format(
                    top_k=top_k,
                    dense="Y" if trace["dense_pool"]["present"] else "n",
                    dist="Y" if trace["distance_pass"]["present"] else "n",
                    final="Y" if trace["final"]["present"] else "n",
                    rank=trace["final"]["rank"] or "-",
                    pairs=trace["hybrid_n"],
                    ms=trace["rerank_ms"],
                )
            )

    out_path = Path(settings.eval_results_dir) / "trace_topk_sweep.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
