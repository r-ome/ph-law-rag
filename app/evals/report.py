import json
from pathlib import Path
from collections import defaultdict

from app.config import settings

def abstentation_accuracy(results: list[dict]) -> dict:
    """Out of scope rows SHOULD abstain; everything else should NOT."""
    correct = total = 0
    for r in results:
        should_abstain = r["category"] == "out-of-scope"
        if r["abstained"] == should_abstain:
            correct += 1
        total += 1
    return { "correct": correct, "total": total, "accuracy": correct / total if total else 0.0 }

def print_report(results: list[dict], scored) -> None:
    ragas_result, scorable = scored

    print("\n=== ABSTENTATION ===")
    ab = abstentation_accuracy(results)
    print(f" correct abstention decisions: {ab['correct']}/{ab['total']} ({ab['accuracy']:.0%})")

    if ragas_result is None:
        print("\n=== RAGAS === \n no scorable (non-abstained rows)")
        return

    df = ragas_result.to_pandas()
    metric_cols = list(df.select_dtypes(include="number").columns)

    df["category"] = [r["category"] for r in scorable]

    print("\n=== RAGAS overall ===")
    for m in metric_cols:
        print(f" {m:36} {df[m].mean():.3f}")

    print("\n=== RAGAS by category ===")
    for cat, grp in df.groupby("category"):
        scores = " ".join(f"{m}={grp[m].mean():.2f}" for m in metric_cols)
        print(f" {cat:12} (n={len(grp)}) {scores}")


def save_scored(results: list[dict], scored) -> None:
    ragas_result, scorable = scored
    out_dir = Path(settings.eval_results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if ragas_result is not None:
        ragas_result.to_pandas().to_json(
            out_dir / "scored_latest.json",
            orient="records",
            indent=2
        )
