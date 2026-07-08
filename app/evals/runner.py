import json
import time
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.evals import artifacts
from app.retriever.answer_service import answer

def load_dataset(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items

def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _active_config() -> dict:
    return {
        "llm_model": settings.llm_model,
        "query_decomposition_enabled": settings.query_decomposition_enabled,
        "reranker_backend": settings.reranker_backend,
        "bedrock_rerank_model": settings.bedrock_rerank_model,
        "bedrock_rerank_region": settings.bedrock_rerank_region,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "qdrant_collection": settings.qdrant_collection,
        "dense_top_k": settings.dense_top_k,
        "sparse_top_k": settings.sparse_top_k,
        "rerank_top_n": settings.rerank_top_n,
        "retrieval_operative_only": settings.retrieval_operative_only,
        "parent_expansion_enabled": settings.parent_expansion_enabled,
        "prefer_operative_enabled": settings.prefer_operative_enabled,
        "consolidated_dedup_enabled": settings.consolidated_dedup_enabled,
        "edge_expansion_enabled": settings.edge_expansion_enabled,
        "answerability_gate_enabled": settings.answerability_gate_enabled,
        "faithfulness_selfcheck_enabled": settings.faithfulness_selfcheck_enabled,
        "later_enacted_preference_enabled": settings.later_enacted_preference_enabled,
        "subquery_packaging_enabled": settings.subquery_packaging_enabled,
    }


def run_rows(
    rows: list[dict],
    out_path: Path,
    *,
    strategy_override: str | None = None,
    trace_label: str | None = "eval",
) -> list[dict]:
    results = []
    total = len(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for i, item in enumerate(rows, start=1):
        start = time.perf_counter()
        resp = answer(
            item["question"],
            debug=True,
            trace_label=trace_label,
            strategy_override=strategy_override,
        )
        elapsed = time.perf_counter() - start
        debug_chunks = resp.get("debug", {}).get("chunks", [])
        debug_stages = resp.get("debug", {}).get("stages", [])
        row = {
            **({"eval_id": item["eval_id"]} if "eval_id" in item else {}),
            "question": item["question"],
            "answer": resp["answer"],
            "contexts": resp["contexts"],
            "selected_chunk_ids": [c["chunk_id"] for c in debug_chunks],
            "debug_stages": debug_stages,
            "ground_truth": item["ground_truth"],
            "expected_sources": item.get("expected_sources", []),
            "category": item["category"],
            "abstained": resp["abstained"],
            "model": settings.llm_model,
            "query_decomposition": settings.query_decomposition_enabled,
            "elapsed_s": round(elapsed, 2),
            "cited_sources": [s.get("source_id", "") for s in resp.get("sources", [])],
            "context_sources": resp.get("context_sources", []),
            # Backward-compatible name for older analysis scripts. This now means
            # the sources present in final context, not only citations exposed
            # after generation.
            "retrieved_sources": resp.get("context_sources", []),
        }
        results.append(row)
        # Append incrementally: a crash or kill hours into a run must not lose computed
        # rows (a 5h40m all-or-nothing run died to swap pressure on 2026-07-03).
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        flag = "ABSTAIN" if row["abstained"] else "answered"
        print(f"[{i}/{total}] {row['category']:12} {flag:8} {elapsed:6.2f}s", flush=True)

    return results


def run_eval_set() -> tuple[list[dict], Path, str]:
    from app.observability.logger import configure_logging

    configure_logging()

    dataset = load_dataset(settings.eval_dataset_path)

    started_at = datetime.now().astimezone()
    model_slug = settings.llm_model.replace(":", "-").replace("/", "-")
    run_tag = artifacts.make_run_tag(model_slug, settings.eval_run_label, started_at)
    paths = artifacts.create_run_paths(run_tag, started_at)
    out_path = paths.run

    # Print the effective config BEFORE warmup: warmup already exercises the reranker
    # (a remote backend spends money on it), and a stale .env has silently confounded a
    # run before (dense_top_k=10 during the Haiku A/B). Eyeball this, then let it spend.
    print("Active config:")
    print(json.dumps(_active_config(), indent=2), flush=True)

    answer("warmup", trace=False)  # prime reranker + Ollama so row 1 isn't cold-start inflated

    results = run_rows(dataset, out_path)

    times = [r["elapsed_s"] for r in results]
    print(f"\nTiming — median {statistics.median(times):.2f}s | mean {statistics.mean(times):.2f}s | "
          f"min {min(times):.2f}s | max {max(times):.2f}s | total {sum(times):.1f}s")

    from app.retriever.reranker import rerank_timings_ms
    if rerank_timings_ms:
        ms = rerank_timings_ms
        print(f"Rerank ({settings.reranker_backend}) — median {statistics.median(ms):.0f}ms | "
              f"mean {statistics.mean(ms):.0f}ms | min {min(ms):.0f}ms | max {max(ms):.0f}ms | n={len(ms)}")

    meta = {
        "tag": run_tag,
        "date": started_at.strftime("%Y-%m-%d"),
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "model": settings.llm_model,
        "model_slug": model_slug,
        "label": settings.eval_run_label,
        "question_count": len(results),
        "scored_count": None,
        "git_sha": _git_sha(),
        "active_config": _active_config(),
    }
    artifacts.save_meta(run_tag, meta)
    artifacts.update_manifest(run_tag, meta=meta)
    artifacts.write_latest(run_tag)

    return results, out_path, run_tag
