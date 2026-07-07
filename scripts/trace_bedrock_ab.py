from __future__ import annotations

# Bedrock reranker preflight (A/B phase 2): run the 9 selector-A/B probes through the
# SHIPPED rerank() path under reranker_backend=bedrock and compare target ranks against
# the qwen_top8_no_margin arm in trace_selector_ab_top30_all.json. Gate: bedrock places
# the targets at/near qwen3's ranks, else stop before judge spend.
#
# ROWS are copied from trace_selector_ab.py (data only — that script's scoring flow
# predates the production backend and must not be reused).
#
# Run: reranker_backend=bedrock AWS_PROFILE=ph-law-rag-dev uv run python scripts/trace_bedrock_ab.py
# Cost: one Bedrock Rerank call per row (~$0.009 total). The full-pool ordering and the
# shipped top-8 come from the same call: the bedrock branch scores every candidate, so
# the top-8 slice of the full ordering IS the shipped selection.

import json
import time
from pathlib import Path

from app.config import settings
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.types import RetrievalResult


REFERENCE_PATH = Path("data/eval_results/traces/trace_selector_ab_top30_all.json")
REFERENCE_ARM = "qwen_top8_no_margin"
OUT_PATH = Path("data/eval_results/traces/trace_bedrock_ab.json")

ROWS = [
    {
        "key": "felony_art3",
        "group": "primary",
        "question": "How does the Revised Penal Code define a felony?",
        "targets": [{"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:3"}],
    },
    {
        "key": "sale_perfection_art1475",
        "group": "primary",
        "question": "When I buy something, at what point is the sale actually a done deal?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1475"}],
    },
    {
        "key": "drug_possession_sec11",
        "group": "must_not_regress",
        "question": "What can happen to me if I'm caught holding illegal drugs?",
        "targets": [{"source_id": "dangerous_drugs_act", "provision_id": "dangerous_drugs_act:article-ii:section:11"}],
    },
    {
        "key": "verbal_land_sale_art1403",
        "group": "must_not_regress",
        "question": "Is a purely verbal agreement to sell a parcel of land valid in the Philippines?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1403"}],
    },
    {
        "key": "theft_penalty_today",
        "group": "ok_spotcheck",
        "question": "How is the penalty for theft determined under the Revised Penal Code today, and how has that changed?",
        "targets": [
            {"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:309"},
            {"source_id": "rpc_penalty_amendments_2017", "provision_id": "revised_penal_code:article:309"},
        ],
    },
    {
        "key": "article_335_rape",
        "group": "ok_spotcheck",
        "question": "Is rape still prosecuted under Article 335 of the Revised Penal Code?",
        "targets": [
            {"source_id": "anti_rape_law_1997", "provision_id": "revised_penal_code:article:266-a"},
            {"source_id": "statutory_rape_amendments_2022", "provision_id": "revised_penal_code:article:266-a"},
        ],
    },
    {
        "key": "statutory_rape_close_age",
        "group": "ok_spotcheck",
        "question": "Is there an exception to statutory rape when the offender and the minor are close in age?",
        "targets": [{"source_id": "statutory_rape_amendments_2022", "provision_id": "revised_penal_code:article:266-a"}],
    },
    {
        "key": "death_penalty_instead",
        "group": "ok_spotcheck",
        "question": "Several provisions of the Revised Penal Code still prescribe the death penalty. Can Philippine courts impose it, and what penalty is imposed instead?",
        "targets": [
            {"source_id": "death_penalty_prohibition", "provision_id": "death_penalty_prohibition:section:1"},
            {"source_id": "death_penalty_prohibition", "provision_id": "death_penalty_prohibition:section:2"},
        ],
    },
    {
        "key": "probation_appeal",
        "group": "ok_spotcheck",
        "question": "Can a defendant who appealed their conviction still apply for probation?",
        "targets": [{"source_id": "probation_amendments_2015", "provision_id": "probation_law:section:4"}],
    },
]


def _clone(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        RetrievalResult(result.chunk_id, result.text, result.score, dict(result.metadata))
        for result in results
    ]


def _matches(result: RetrievalResult, targets: list[dict[str, str]]) -> bool:
    return any(
        result.metadata.get("source_id") == target["source_id"]
        and result.metadata.get("provision_id") == target["provision_id"]
        for target in targets
    )


def _first_target(results: list[RetrievalResult], targets: list[dict[str, str]]) -> dict:
    for rank, result in enumerate(results, start=1):
        if _matches(result, targets):
            return {
                "present": True,
                "rank": rank,
                "chunk_id": result.chunk_id,
                "source_id": result.metadata.get("source_id"),
                "provision_id": result.metadata.get("provision_id"),
                "unit_label": result.metadata.get("unit_label"),
                "score": result.score,
            }
    return {"present": False, "rank": None}


def _full_rerank(question: str, candidates: list[RetrievalResult]) -> list[RetrievalResult]:
    original_top_n = settings.rerank_top_n
    try:
        settings.rerank_top_n = len(candidates)
        return rerank(question, _clone(candidates))
    finally:
        settings.rerank_top_n = original_top_n


def _load_reference() -> dict[str, dict]:
    rows = json.loads(REFERENCE_PATH.read_text())
    reference = {}
    for row in rows:
        arm = next(a for a in row["arms"] if a["arm"] == REFERENCE_ARM)
        reference[row["key"]] = arm["target"]
    return reference


def main() -> None:
    assert settings.reranker_backend == "bedrock", (
        f"run with reranker_backend=bedrock (got {settings.reranker_backend!r})"
    )
    reference = _load_reference()

    output_rows = []
    print(f"backend=bedrock model={settings.bedrock_rerank_model} dense_top_k={settings.dense_top_k}")
    print(f"reference arm: {REFERENCE_ARM} (2026-07-03 index — compare ranks, chunk_ids may be stale)\n")
    header = f"{'key':26} {'group':16} {'qwen':>5} {'bdrk8':>6} {'full':>5} {'ms':>6}  verdict"
    print(header)
    print("-" * len(header))

    for row in ROWS:
        candidates = hybrid_retriever(row["question"])
        start = time.perf_counter()
        full = _full_rerank(row["question"], candidates)
        elapsed_ms = (time.perf_counter() - start) * 1000
        selected = full[: settings.rerank_top_n]

        candidate_hit = _first_target(candidates, row["targets"])
        selected_hit = _first_target(selected, row["targets"])
        full_hit = _first_target(full, row["targets"])
        qwen_rank = reference.get(row["key"], {}).get("rank")

        if selected_hit["present"] and (qwen_rank is None or selected_hit["rank"] <= qwen_rank):
            verdict = "ok"
        elif selected_hit["present"]:
            verdict = "ok_worse_rank"
        elif qwen_rank is None:
            verdict = "miss_same_as_qwen"
        elif not candidate_hit["present"]:
            verdict = "POOL_MISS"
        else:
            verdict = "SELECTOR_CUT"

        output_rows.append(
            {
                **{k: row[k] for k in ("key", "group", "question", "targets")},
                "candidate_count": len(candidates),
                "candidate_hit": candidate_hit,
                "selected_hit": selected_hit,
                "full_rerank_hit": full_hit,
                "qwen_reference_rank": qwen_rank,
                "elapsed_ms": round(elapsed_ms, 1),
                "verdict": verdict,
            }
        )
        print(
            f"{row['key']:26} {row['group']:16} "
            f"{qwen_rank if qwen_rank is not None else '-':>5} "
            f"{selected_hit['rank'] if selected_hit['present'] else '-':>6} "
            f"{full_hit['rank'] if full_hit['present'] else '-':>5} "
            f"{elapsed_ms:6.0f}  {verdict}",
            flush=True,
        )

    result = {
        "reference_arm": REFERENCE_ARM,
        "reranker_backend": settings.reranker_backend,
        "bedrock_rerank_model": settings.bedrock_rerank_model,
        "dense_top_k": settings.dense_top_k,
        "rerank_top_n": settings.rerank_top_n,
        "rows": output_rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2))

    bad = [r["key"] for r in output_rows if r["verdict"] in ("POOL_MISS", "SELECTOR_CUT")]
    print(f"\nWrote {OUT_PATH}")
    print("GATE:", "FAIL — " + ", ".join(bad) if bad else "PASS — no selector cuts or pool misses")


if __name__ == "__main__":
    main()
