import json
import time
import statistics
from pathlib import Path

from app.config import settings
from app.retriever.answer_service import answer

def load_dataset(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items

def run_eval_set() -> tuple[list[dict], Path]:
    dataset = load_dataset(settings.eval_dataset_path)
    results = []

    out_dir = Path(settings.eval_results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_slug = settings.llm_model.replace(":", "-").replace("/", "-")
    label = f"_{settings.eval_run_label}" if settings.eval_run_label else ""
    out_path = out_dir / f"run_{model_slug}{label}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    answer("warmup")  # prime reranker + Ollama so row 1 isn't cold-start inflated

    for i, item in enumerate(dataset, start=1):
        start = time.perf_counter()
        resp = answer(item["question"])
        elapsed = time.perf_counter() - start
        row = {
            "question": item["question"],
            "answer": resp["answer"],
            "contexts": resp["contexts"],
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
        print(f"[{i}/{len(dataset)}] {row['category']:12} {flag:8} {elapsed:6.2f}s", flush=True)

    times = [r["elapsed_s"] for r in results]
    print(f"\nTiming — median {statistics.median(times):.2f}s | mean {statistics.mean(times):.2f}s | "
          f"min {min(times):.2f}s | max {max(times):.2f}s | total {sum(times):.1f}s")

    from app.retriever.reranker import rerank_timings_ms
    if rerank_timings_ms:
        ms = rerank_timings_ms
        print(f"Rerank ({settings.reranker_backend}) — median {statistics.median(ms):.0f}ms | "
              f"mean {statistics.mean(ms):.0f}ms | min {min(ms):.0f}ms | max {max(ms):.0f}ms | n={len(ms)}")

    return results, out_path
