"""Full 70-Q local run, abstention-only — NO RAGAS / NO haiku.

Calls run_eval_set() (generation path only), then computes abstention accuracy
by category and diffs per-question outcomes against a prior baseline run.

    uv run python scripts/run_abstention.py data/eval_results/run_mistral_20260613_191727.jsonl
"""
import json
import sys
from collections import defaultdict

from app.evals.runner import run_eval_set


def load(path):
    with open(path, encoding="utf-8") as f:
        return {json.loads(l)["question"]: json.loads(l) for l in f if l.strip()}


def main(baseline_path: str | None):
    results, out_path, run_tag = run_eval_set()
    print(f"\nrun written: {out_path}")
    print(f"run tag: {run_tag}")

    by_cat = defaultdict(lambda: [0, 0])  # [abstained, total]
    oos_leaks, nonoos_abstains = [], []
    for r in results:
        cat = r["category"]
        by_cat[cat][1] += 1
        if r["abstained"]:
            by_cat[cat][0] += 1
        is_oos = cat == "out-of-scope"
        if is_oos and not r["abstained"]:
            oos_leaks.append(r["question"])
        if not is_oos and r["abstained"]:
            nonoos_abstains.append((cat, r["question"]))

    print("\n=== ABSTENTION by category (abstained/total) ===")
    correct = 0
    for cat, (ab, tot) in sorted(by_cat.items()):
        good = ab if cat == "out-of-scope" else tot - ab
        correct += good
        print(f"  {cat:13} abstained {ab}/{tot}  correct {good}/{tot}")
    print(f"  TOTAL correct abstention decisions: {correct}/{len(results)}")

    print(f"\n=== OOS LEAKS (answered, should abstain): {len(oos_leaks)} ===")
    for q in oos_leaks:
        print(f"  - {q}")
    print(f"\n=== NON-OOS ABSTAINS (possible false abstain): {len(nonoos_abstains)} ===")
    for cat, q in nonoos_abstains:
        print(f"  - [{cat}] {q}")

    if baseline_path:
        base = load(baseline_path)
        print(f"\n=== PER-QUESTION DIFF vs {baseline_path} ===")
        for r in results:
            q = r["question"]
            b = base.get(q)
            if b is None:
                continue
            if b["abstained"] != r["abstained"]:
                arrow = ("answered -> ABSTAIN" if r["abstained"]
                         else "ABSTAIN -> answered")
                print(f"  [{r['category']:13}] {arrow}: {q}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
