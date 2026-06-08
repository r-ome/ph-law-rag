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
            "elapsed_s": round(elapsed, 2),
            "retrieved_sources": [s.get("source_id", "") for s in resp.get("sources", [])]
        }
        results.append(row)
        flag = "ABSTAIN" if row["abstained"] else "answered"
        print(f"[{i}/{len(dataset)}] {row['category']:12} {flag:8} {elapsed:6.2f}s")

    out_dir = Path(settings.eval_results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_slug = settings.llm_model.replace(":", "-").replace("/", "-")
    out_path = out_dir /f"run_{model_slug}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    times = [r["elapsed_s"] for r in results]
    print(f"\nTiming — median {statistics.median(times):.2f}s | mean {statistics.mean(times):.2f}s | "
          f"min {min(times):.2f}s | max {max(times):.2f}s | total {sum(times):.1f}s")

    return results, out_path
