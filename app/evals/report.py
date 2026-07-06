from datetime import datetime

from app.evals import artifacts

def abstention_accuracy(results: list[dict]) -> dict:
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

    print("\n=== ABSTENTION ===")
    ab = abstention_accuracy(results)
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


def build_summary(results: list[dict], scored) -> dict:
    """The aggregate report (abstention + RAGAS overall + by category) as a
    serializable dict, so the numbers printed by print_report are also durable
    and diffable across runs."""
    ragas_result, scorable = scored
    summary = {
        "abstention": abstention_accuracy(results),
        "overall": {},
        "by_category": {},
    }
    if ragas_result is None:
        return summary

    df = ragas_result.to_pandas()
    metric_cols = list(df.select_dtypes(include="number").columns)
    df["category"] = [r["category"] for r in scorable]

    summary["overall"] = {m: round(float(df[m].mean()), 4) for m in metric_cols}
    for cat, grp in df.groupby("category"):
        summary["by_category"][cat] = {
            "n": int(len(grp)),
            **{m: round(float(grp[m].mean()), 4) for m in metric_cols},
        }
    return summary


def save_scored(results: list[dict], scored, run_tag: str | None = None) -> None:
    ragas_result, scorable = scored
    tag = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = artifacts.paths_for_tag(tag)
    summary = build_summary(results, scored)

    artifacts.write_json(paths.summary, summary)

    if ragas_result is not None:
        df = ragas_result.to_pandas()
        paths.scored.parent.mkdir(parents=True, exist_ok=True)
        df.to_json(paths.scored, orient="records", indent=2)

    meta = artifacts.load_meta(tag)
    if meta is not None:
        meta["scored_count"] = len(scorable)
        meta["scored_at"] = datetime.now().astimezone().isoformat()
        artifacts.save_meta(tag, meta)

    artifacts.update_manifest(tag, meta=meta, summary=summary)
    artifacts.write_latest(tag)
