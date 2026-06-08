import json
import time
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

    for i, item in enumerate(dataset, start=1):
        resp = answer(item["question"])
        row = {
            "question": item["question"],
            "answer": resp["answer"],
            "contexts": resp["contexts"],
            "ground_truth": item["ground_truth"],
            "expected_sources": item.get("expected_sources", []),
            "category": item["category"],
            "abstained": resp["abstained"],
            "retrieved_sources": [s.get("source_id", "") for s in resp.get("sources", [])]
        }
        results.append(row)
        flag = "ABSTAIN" if row["abstained"] else "answered"
        print(f"[{i}/{len(dataset)}] {row['category']:12} {flag}")

    out_dir = Path(settings.eval_results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir /f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return results, out_path
