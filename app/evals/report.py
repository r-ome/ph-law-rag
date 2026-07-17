from datetime import datetime

from app.evals import artifacts

def abstention_accuracy(results: list[dict]) -> dict:
    """Out of scope rows SHOULD abstain; everything else should NOT."""
    total = len(results)
    abstained = sum(bool(row["abstained"]) for row in results)
    expected = [row for row in results if row["category"] == "out-of-scope"]
    expected_answers = [row for row in results if row["category"] != "out-of-scope"]
    correct_abstentions = sum(bool(row["abstained"]) for row in expected)
    false_abstentions = sum(bool(row["abstained"]) for row in expected_answers)
    answer_leaks = sum(not row["abstained"] for row in expected)
    correct = correct_abstentions + sum(not row["abstained"] for row in expected_answers)
    return {
        "correct": correct,
        "total": total,
        "answered": total - abstained,
        "abstained": abstained,
        "abstain_count": abstained,
        "expected_abstentions": len(expected),
        "correct_abstentions": correct_abstentions,
        "false_abstentions": false_abstentions,
        "answer_leaks": answer_leaks,
        "target_present_despite_abstention": sum(
            bool(row["abstained"]) and bool(row.get("retrieval_target_present"))
            for row in results
        ),
        "accuracy": correct / total if total else 0.0,
    }


def _category_counts(results: list[dict]) -> dict:
    return abstention_accuracy(results)

def _is_holdout(results: list[dict]) -> bool:
    return any(row.get("split") == "holdout" for row in results)


def print_report(results: list[dict], scored) -> None:
    ragas_result, scorable = scored

    print("\n=== ABSTENTION ===")
    ab = abstention_accuracy(results)
    print(f" correct abstention decisions: {ab['correct']}/{ab['total']} ({ab['accuracy']:.0%})")
    print(
        f" answered: {ab['answered']} | abstained: {ab['abstained']} | "
        f"false abstentions: {ab['false_abstentions']} | answer leaks: {ab['answer_leaks']}"
    )
    if _is_holdout(results):
        print(f" abstain count: {ab['abstain_count']}")
    else:
        print("\n=== ALL ROWS BY CATEGORY ===")
        for category in sorted({row["category"] for row in results}):
            counts = _category_counts(
                [row for row in results if row["category"] == category]
            )
            print(
                f" {category:12} total={counts['total']} answered={counts['answered']} "
                f"abstained={counts['abstained']} expected_abstentions="
                f"{counts['expected_abstentions']} correct_abstentions="
                f"{counts['correct_abstentions']} false_abstentions="
                f"{counts['false_abstentions']} answer_leaks={counts['answer_leaks']} "
                f"target_present_despite_abstention="
                f"{counts['target_present_despite_abstention']}"
            )

    if ragas_result is None:
        print("\n=== RAGAS === \n no scorable (non-abstained rows)")
        return

    df = ragas_result.to_pandas()
    metric_cols = list(df.select_dtypes(include="number").columns)

    df["category"] = [r["category"] for r in scorable]

    print("\n=== RAGAS overall ===")
    print(f" n={len(scorable)} answered rows scored")
    for m in metric_cols:
        print(f" {m:36} {df[m].mean():.3f}")

    if _is_holdout(results):
        return

    print("\n=== RAGAS by category ===")
    for cat, grp in df.groupby("category"):
        scores = " ".join(f"{m}={grp[m].mean():.2f}" for m in metric_cols)
        print(f" {cat:12} (n={len(grp)}) {scores}")


def build_summary(results: list[dict], scored, *, holdout: bool | None = None) -> dict:
    """The aggregate report (abstention + RAGAS overall + by category) as a
    serializable dict, so the numbers printed by print_report are also durable
    and diffable across runs."""
    ragas_result, scorable = scored
    holdout = _is_holdout(results) if holdout is None else holdout
    summary = {
        "abstention": abstention_accuracy(results),
        "overall": {"n": 0, "all_rows": len(results)},
        "by_category": {},
    }
    categories = sorted({row["category"] for row in results})
    for category in categories:
        category_rows = [row for row in results if row["category"] == category]
        summary["by_category"][category] = {
            "n": 0,
            "all_rows": len(category_rows),
            **_category_counts(category_rows),
        }
    if holdout:
        summary["by_category"] = {}
    if ragas_result is None:
        return summary

    df = ragas_result.to_pandas()
    metric_cols = list(df.select_dtypes(include="number").columns)
    df["category"] = [r["category"] for r in scorable]

    summary["overall"] = {
        "n": len(scorable),
        "all_rows": len(results),
        **{m: round(float(df[m].mean()), 4) for m in metric_cols},
    }
    if not holdout:
        for cat, grp in df.groupby("category"):
            summary["by_category"][cat].update({
                "n": int(len(grp)),
                **{m: round(float(grp[m].mean()), 4) for m in metric_cols},
            })
    else:
        summary["by_category"] = {}
    return summary


def save_scored(results: list[dict], scored, run_tag: str | None = None) -> None:
    ragas_result, scorable = scored
    tag = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = artifacts.paths_for_tag(tag)
    meta = artifacts.load_meta(tag)
    holdout = bool((meta or {}).get("holdout")) or _is_holdout(results)
    summary = build_summary(results, scored, holdout=holdout)

    artifacts.write_json(paths.summary, summary)

    if ragas_result is not None:
        df = ragas_result.to_pandas()
        # Persist the stable ID alongside score rows. RAGAS itself is intentionally
        # content-addressed, so IDs belong in artifacts rather than its cache key.
        df["eval_id"] = [row.get("eval_id") for row in scorable]
        paths.scored.parent.mkdir(parents=True, exist_ok=True)
        df.to_json(paths.scored, orient="records", indent=2)

    if meta is not None:
        meta["scored_count"] = len(scorable)
        meta["scored_at"] = datetime.now().astimezone().isoformat()
        try:
            from app.evals.ragas_scorer import scoring_identity

            meta["scoring_identity"] = scoring_identity(
                generator_model=meta.get("generator_model") or meta.get("model"),
                use_cache=True,
            )
        except Exception:
            pass
        artifacts.save_meta(tag, meta)

    artifacts.update_manifest(tag, meta=meta, summary=summary)
    artifacts.write_latest(tag)
