from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.evals import artifacts
from app.evals.intent_labels import load_intent_labels
from app.evals.runner import _active_config, load_dataset, run_rows
from app.retriever.intent_router import INTENT_TO_STRATEGY, classify_with_raw, render_llm_prompts
from app.retriever.strategy import resolve_knobs


EXPECTED_BASELINE_KNOBS = {
    "dense_top_k": 30,
    "sparse_top_k": 10,
    "rerank_top_n": 8,
    "parent_expansion_enabled": True,
    "prefer_operative_enabled": False,
    "retrieval_operative_only": True,
    "consolidated_dedup_enabled": True,
}

EXPECTED_ACTIVE_CONFIG = {
    "llm_model": "mistral",
    "query_decomposition_enabled": False,
    "reranker_backend": "minilm",
    "bedrock_rerank_model": "amazon.rerank-v1:0",
    "bedrock_rerank_region": "us-west-2",
    "embedding_backend": "ollama",
    "embedding_model": "nomic-embed-text",
    "qdrant_collection": "ph_law",
    "dense_top_k": 30,
    "sparse_top_k": 10,
    "rerank_top_n": 8,
    "retrieval_operative_only": True,
    "parent_expansion_enabled": True,
    "prefer_operative_enabled": False,
    "consolidated_dedup_enabled": True,
    "edge_expansion_enabled": True,
    "answerability_gate_enabled": False,
    "faithfulness_selfcheck_enabled": False,
    "later_enacted_preference_enabled": False,
    "subquery_packaging_enabled": False,
}

METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "llm_context_precision_with_reference",
    "context_recall",
]


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _prompt_hash() -> str:
    system, user = render_llm_prompts("{question}")
    return hashlib.sha256(f"SYSTEM:\n{system}\nUSER:\n{user}".encode()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _create_run_dir(started_at: datetime) -> tuple[str, Path]:
    tag = f"r5_predicted_strategy_{started_at.strftime('%Y%m%d_%H%M%S')}"
    run_dir = artifacts.results_dir() / "runs" / started_at.strftime("%Y-%m-%d") / tag
    run_dir.mkdir(parents=True, exist_ok=False)
    return tag, run_dir


def _load_rows() -> list[dict[str, Any]]:
    rows = load_dataset(settings.eval_dataset_path)
    return [{**row, "eval_id": f"eval_{i:03d}"} for i, row in enumerate(rows, start=1)]


def _assert_profile(allow_nonstandard_profile: bool) -> dict[str, Any]:
    active = _active_config()
    baseline = resolve_knobs("default").as_trace_dict()
    mismatches = {
        key: {"expected": expected, "actual": active.get(key)}
        for key, expected in EXPECTED_ACTIVE_CONFIG.items()
        if active.get(key) != expected
    }
    knob_mismatches = {
        key: {"expected": expected, "actual": baseline.get(key)}
        for key, expected in EXPECTED_BASELINE_KNOBS.items()
        if baseline.get(key) != expected
    }
    if (mismatches or knob_mismatches) and not allow_nonstandard_profile:
        raise SystemExit(
            "R5 profile mismatch before spend. Re-run with the intended env "
            "(notably RERANKER_BACKEND=minilm) or pass "
            "--allow-nonstandard-profile.\n"
            + json.dumps(
                {"active_config": mismatches, "baseline_knobs": knob_mismatches},
                indent=2,
            )
        )
    return {
        "active_config": active,
        "expected_active_config": EXPECTED_ACTIVE_CONFIG,
        "baseline_knobs": baseline,
        "expected_baseline_knobs": EXPECTED_BASELINE_KNOBS,
        "profile_mismatches": mismatches,
        "baseline_knob_mismatches": knob_mismatches,
        "allow_nonstandard_profile": allow_nonstandard_profile,
    }


def _run_router_sweep(rows: list[dict[str, Any]], labels: dict[str, str], out_path: Path) -> list[dict[str, Any]]:
    sweep_rows: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        decision, raw = classify_with_raw(row["question"])
        gold_intent = labels[row["question"]]
        gold_strategy = INTENT_TO_STRATEGY[gold_intent]
        sweep_row = {
            "eval_id": row["eval_id"],
            "question": row["question"],
            "raw": raw,
            "gold_intent": gold_intent,
            "gold_strategy": gold_strategy,
            "intent": decision.intent,
            "confidence": decision.confidence,
            "routed_intent": decision.routed_intent,
            "strategy": decision.strategy,
            "parse_ok": decision.parse_ok,
            "fallback_reason": decision.fallback_reason,
            "error": decision.error,
            "latency_ms": decision.latency_ms,
        }
        sweep_rows.append(sweep_row)
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sweep_row, ensure_ascii=False) + "\n")
        print(
            f"[ROUTER {i}/{len(rows)}] {row['eval_id']} "
            f"gold={gold_intent} predicted={decision.routed_intent}",
            flush=True,
        )
    return sweep_rows


def _router_metrics(sweep_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(sweep_rows)
    strategy_correct = sum(1 for row in sweep_rows if row["strategy"] == row["gold_strategy"])
    gold_current = [row for row in sweep_rows if row["gold_strategy"] == "current_law"]
    pred_current = [row for row in sweep_rows if row["strategy"] == "current_law"]
    true_current = [
        row for row in pred_current
        if row["gold_strategy"] == "current_law"
    ]
    false_current = [
        row for row in pred_current
        if row["gold_strategy"] != "current_law"
    ]
    return {
        "strategy_accuracy": strategy_correct / total if total else 0.0,
        "strategy_correct": strategy_correct,
        "total": total,
        "amendment_precision": len(true_current) / len(pred_current) if pred_current else None,
        "amendment_recall": len(true_current) / len(gold_current) if gold_current else None,
        "amendment_true_positive_count": len(true_current),
        "amendment_gold_count": len(gold_current),
        "amendment_predicted_count": len(pred_current),
        "false_current_law_fires": [
            {"eval_id": row["eval_id"], "question": row["question"], "gold_intent": row["gold_intent"]}
            for row in false_current
        ],
    }


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["eval_id"]: row for row in rows}


def _first_ranks(values: list[str], targets: list[str]) -> dict[str, int | None]:
    ranks: dict[str, int | None] = {}
    for target in targets:
        try:
            ranks[target] = values.index(target) + 1
        except ValueError:
            ranks[target] = None
    return ranks


def _stage_fired(row: dict[str, Any], stage_name: str) -> bool | None:
    for stage in row.get("debug_stages", []):
        if stage.get("name") == stage_name and "fired" in stage:
            return bool(stage["fired"])
    return None


def build_context_diff(
    candidate_ids: set[str],
    rows_by_id: dict[str, dict[str, Any]],
    baseline_by_id: dict[str, dict[str, Any]],
    current_by_id: dict[str, dict[str, Any]],
    sweep_by_id: dict[str, dict[str, Any]],
    gold_current_ids: set[str],
    predicted_current_ids: set[str],
) -> list[dict[str, Any]]:
    diff_rows: list[dict[str, Any]] = []
    for eval_id in sorted(candidate_ids):
        base = baseline_by_id[eval_id]
        current = current_by_id[eval_id]
        expected_sources = rows_by_id[eval_id].get("expected_sources", [])
        changed = base["selected_chunk_ids"] != current["selected_chunk_ids"]
        diff_rows.append(
            {
                "eval_id": eval_id,
                "question": rows_by_id[eval_id]["question"],
                "gold_current_law": eval_id in gold_current_ids,
                "predicted_current_law": eval_id in predicted_current_ids,
                "gold_intent": sweep_by_id[eval_id]["gold_intent"],
                "predicted_intent": sweep_by_id[eval_id]["routed_intent"],
                "prefer_operative_fired": _stage_fired(current, "prefer_operative"),
                "changed": changed,
                "in_changed_universe": changed,
                "false_fire_changed": (
                    eval_id in predicted_current_ids
                    and eval_id not in gold_current_ids
                    and changed
                ),
                "baseline_selected_chunk_ids": base["selected_chunk_ids"],
                "current_law_selected_chunk_ids": current["selected_chunk_ids"],
                "target_ranks": {
                    "expected_sources": expected_sources,
                    "baseline": _first_ranks(base.get("context_sources", []), expected_sources),
                    "current_law": _first_ranks(current.get("context_sources", []), expected_sources),
                },
            }
        )
    return diff_rows


def compose_arm_rows(
    universe_ids: set[str],
    baseline_by_id: dict[str, dict[str, Any]],
    current_by_id: dict[str, dict[str, Any]],
    swap_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for eval_id in sorted(universe_ids):
        source = current_by_id[eval_id] if eval_id in swap_ids else baseline_by_id[eval_id]
        arm_row = dict(source)
        arm_row["r5_source_arm"] = "current_law" if eval_id in swap_ids else "baseline"
        rows.append(arm_row)
    return rows


def _write_scored(path: Path, scored: tuple[Any, list[dict[str, Any]]]) -> None:
    ragas_result, _scorable = scored
    if ragas_result is None:
        artifacts.write_json(path, [])
        return
    df = ragas_result.to_pandas()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(path, orient="records", indent=2)


def _metric_deltas(base: dict[str, Any], other: dict[str, Any]) -> dict[str, float | None]:
    base_overall = base.get("overall", {})
    other_overall = other.get("overall", {})
    deltas: dict[str, float | None] = {}
    for metric in METRIC_NAMES:
        if metric in base_overall and metric in other_overall:
            deltas[metric] = round(float(other_overall[metric]) - float(base_overall[metric]), 4)
        else:
            deltas[metric] = None
    return deltas


def _score_arms(
    arm_rows: dict[str, list[dict[str, Any]]],
    run_dir: Path,
    use_cache: bool,
) -> dict[str, Any]:
    from app.evals.ragas_scorer import score
    from app.evals.report import build_summary

    metrics: dict[str, Any] = {}
    for arm, rows in arm_rows.items():
        print(f"\n[JUDGE] {arm}: {len(rows)} changed-context rows", flush=True)
        scored = score(rows, use_cache=use_cache)
        summary = build_summary(rows, scored)
        _write_scored(run_dir / f"scored_{arm}.json", scored)
        metrics[arm] = {
            "row_count": len(rows),
            "scored_count": len(scored[1]),
            "summary": summary,
        }

    if "baseline" in metrics:
        for arm in ("oracle", "predicted"):
            if arm in metrics:
                metrics[arm]["delta_vs_baseline"] = _metric_deltas(
                    metrics["baseline"]["summary"],
                    metrics[arm]["summary"],
                )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run R5 predicted-strategy eval for the current_law lane."
    )
    parser.add_argument(
        "--allow-nonstandard-profile",
        action="store_true",
        help="Run even if the active config differs from the frozen R5 MiniLM profile.",
    )
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="Generate artifacts but skip RAGAS scoring.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not reuse cached RAGAS row scores.",
    )
    args = parser.parse_args()

    profile = _assert_profile(args.allow_nonstandard_profile)
    rows = _load_rows()
    labels = load_intent_labels()
    rows_by_id = _by_id(rows)
    started_at = datetime.now().astimezone()
    run_tag, run_dir = _create_run_dir(started_at)

    meta = {
        "kind": "r5_predicted_strategy",
        "tag": run_tag,
        "date": started_at.strftime("%Y-%m-%d"),
        "started_at": started_at.isoformat(),
        "git_sha": _git_sha(),
        "router_model": settings.router_model,
        "generator_model": settings.llm_model,
        "ragas_llm_model": settings.ragas_llm_model,
        "ragas_embedding_model": settings.ragas_embedding_model,
        "router_prompt_hash": _prompt_hash(),
        "frozen_sweep_note": (
            "Predicted labels are frozen from one classify_with_raw sweep; "
            "the live Haiku router at temperature 0 is not bit-guaranteed."
        ),
        "profile": profile,
        "notes": [
            "No latest.json or manifest.jsonl writes; R5 is a composed strategy artifact.",
            "Changed-context universe U is forced-current_law selected chunk_id diffs over gold-or-predicted candidates.",
            "Identical-context rows keep baseline answer/scores and are excluded from U.",
            "False-fire changed rows stay in U and count against predicted.",
        ],
    }
    artifacts.write_json(run_dir / "meta.json", meta)

    print("Active config:")
    print(json.dumps(profile["active_config"], indent=2), flush=True)

    print("\n[BASELINE] full dataset, forced default", flush=True)
    baseline_rows = run_rows(
        rows,
        run_dir / "run_baseline.jsonl",
        strategy_override="default",
        trace_label="r5_baseline",
    )
    baseline_by_id = _by_id(baseline_rows)

    print("\n[ROUTER] Haiku sweep", flush=True)
    sweep_rows = _run_router_sweep(rows, labels, run_dir / "router_sweep.jsonl")
    sweep_by_id = _by_id(sweep_rows)
    router_metrics = _router_metrics(sweep_rows)

    gold_current_ids = {
        row["eval_id"]
        for row in rows
        if labels[row["question"]] == "amendment_or_current_law"
    }
    predicted_current_ids = {
        row["eval_id"]
        for row in sweep_rows
        if row["strategy"] == "current_law"
    }
    candidate_ids = gold_current_ids | predicted_current_ids
    subset_rows = [rows_by_id[eval_id] for eval_id in sorted(candidate_ids)]

    print("\n[CURRENT_LAW] gold/predicted union subset", flush=True)
    current_rows = run_rows(
        subset_rows,
        run_dir / "run_current_law_subset.jsonl",
        strategy_override="current_law",
        trace_label="r5_current_law_subset",
    )
    current_by_id = _by_id(current_rows)

    context_diff = build_context_diff(
        candidate_ids,
        rows_by_id,
        baseline_by_id,
        current_by_id,
        sweep_by_id,
        gold_current_ids,
        predicted_current_ids,
    )
    artifacts.write_json(run_dir / "context_diff.json", context_diff)

    universe_ids = {
        row["eval_id"]
        for row in context_diff
        if row["changed"]
    }
    oracle_swap_ids = universe_ids & gold_current_ids
    predicted_swap_ids = universe_ids & predicted_current_ids

    arm_rows = {
        "baseline": compose_arm_rows(universe_ids, baseline_by_id, current_by_id, set()),
        "oracle": compose_arm_rows(universe_ids, baseline_by_id, current_by_id, oracle_swap_ids),
        "predicted": compose_arm_rows(universe_ids, baseline_by_id, current_by_id, predicted_swap_ids),
    }
    for arm, composed in arm_rows.items():
        _write_jsonl(run_dir / f"run_{arm}_composed_u.jsonl", composed)

    metrics: dict[str, Any] = {
        "changed_universe": {
            "row_count": len(universe_ids),
            "eval_ids": sorted(universe_ids),
            "candidate_count": len(candidate_ids),
            "gold_current_law_count": len(gold_current_ids),
            "predicted_current_law_count": len(predicted_current_ids),
            "false_fire_changed": [
                {
                    "eval_id": row["eval_id"],
                    "question": row["question"],
                    "gold_intent": row["gold_intent"],
                    "predicted_intent": row["predicted_intent"],
                }
                for row in context_diff
                if row["false_fire_changed"]
            ],
        },
        "router": router_metrics,
        "arms": {},
    }
    if not args.no_score:
        metrics["arms"] = _score_arms(arm_rows, run_dir, use_cache=not args.no_cache)
    else:
        metrics["arms"] = {
            arm: {"row_count": len(composed), "scored_count": None, "summary": None}
            for arm, composed in arm_rows.items()
        }
    metrics["completed_at"] = datetime.now().astimezone().isoformat()
    artifacts.write_json(run_dir / "metrics.json", metrics)

    print("\nR5 complete")
    print(json.dumps({
        "run_dir": str(run_dir),
        "changed_universe_rows": len(universe_ids),
        "router_strategy_accuracy": router_metrics["strategy_accuracy"],
        "false_fire_changed": metrics["changed_universe"]["false_fire_changed"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
