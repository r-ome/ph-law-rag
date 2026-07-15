import json
import time
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.evals import artifacts
from app.pipeline.policy import resolve_policy
from app.pipeline.runner import run_answer

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
    resolution = resolve_policy()
    policy = resolution.policy
    return {
        "profile": policy.name,
        "policy_overrides": resolution.policy_overrides,
        "env_ignored": resolution.env_ignored,
        "llm_model": policy.generator_model,
        "strong_model": policy.strong_model,
        "escalate_intents": sorted(policy.escalate_intents),
        "escalate_on_partial_evidence": policy.escalate_on_partial_evidence,
        "query_decomposition_enabled": policy.query_decomposition_enabled,
        "reranker_backend": settings.reranker_backend,
        "bedrock_rerank_model": settings.bedrock_rerank_model,
        "bedrock_rerank_region": settings.bedrock_rerank_region,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_query_instruction": settings.embedding_query_instruction,
        "qdrant_collection": settings.qdrant_collection,
        "dense_top_k": policy.retrieval_defaults.dense_top_k,
        "sparse_top_k": policy.retrieval_defaults.sparse_top_k,
        "rerank_top_n": policy.retrieval_defaults.rerank_top_n,
        "retrieval_operative_only": policy.retrieval_defaults.retrieval_operative_only,
        "parent_expansion_enabled": policy.retrieval_defaults.parent_expansion_enabled,
        "prefer_operative_enabled": policy.retrieval_defaults.prefer_operative_enabled,
        "consolidated_dedup_enabled": policy.retrieval_defaults.consolidated_dedup_enabled,
        "edge_expansion_enabled": policy.retrieval_defaults.edge_expansion_enabled,
        "evidence_gate": policy.evidence_gate,
        "corrective_retrieval_enabled": policy.corrective_retrieval_enabled,
        "answerability_gate_enabled": policy.evidence_gate == "answerability",
        "faithfulness_selfcheck_enabled": policy.selfcheck_enabled,
        "later_enacted_preference_enabled": policy.later_enacted_preference_enabled,
        "subquery_packaging_enabled": policy.retrieval_defaults.subquery_packaging_enabled,
    }


def run_rows(
    rows: list[dict],
    out_path: Path,
    *,
    debug: bool = True,
    strategy_override: str | None = None,
    trace_label: str | None = "eval",
    holdout: bool = False,
) -> list[dict]:
    from app.evals.retrieval_metrics import save_retrieval_summary
    from app.evals.retrieval_targets import load_retrieval_targets
    from app.evals.retrieval_trace import (
        append_completed_row,
        candidate_count_metadata,
        candidate_lines,
    )

    results = []
    holdout_operational: list[dict] = []
    total = len(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    policy = resolve_policy().policy
    targets_by_id = {} if holdout else load_retrieval_targets()
    if out_path.name == "run.jsonl":
        retrieval_trace_path = out_path.parent / "retrieval_trace.jsonl"
        retrieval_summary_path = out_path.parent / "retrieval_summary.json"
    else:
        tag = artifacts.tag_from_run_path(out_path)
        retrieval_trace_path = out_path.with_name(f"retrieval_trace_{tag}.jsonl")
        retrieval_summary_path = out_path.with_name(f"retrieval_summary_{tag}.json")

    for i, item in enumerate(rows, start=1):
        start = time.perf_counter()
        resp, trace_record = run_answer(
            item["question"],
            debug=debug,
            trace_label=trace_label,
            strategy_override=strategy_override,
            capture_candidate_stages=True,
        )
        if trace_record is None:
            raise RuntimeError("candidate capture did not produce an internal trace")
        elapsed = time.perf_counter() - start
        eval_id = item.get("id", item.get("eval_id"))
        target_record = targets_by_id.get(eval_id)
        trace_lines = (
            []
            if holdout
            else candidate_lines(item, resp, trace_record, target_record)
        )
        target_match_field = (
            "expected_source_match"
            if (target_record or {}).get("match_mode") == "source_only"
            else "expected_provision_match"
        )
        retrieval_target_present = any(
            line.get("stage") in {"selected", "corrective"}
            and line.get(target_match_field) is True
            for line in trace_lines
        )
        debug_chunks = resp.get("debug", {}).get("chunks", [])
        debug_stages = resp.get("debug", {}).get("stages", [])
        model_choice = resp.get("model_choice") or {
            "model": resp.get("generator_model", policy.generator_model),
            "reason": "not_generated" if resp.get("abstained") else "policy_default",
        }
        row = {
            "eval_id": eval_id,
            "question": item["question"],
            "answer": resp["answer"],
            "contexts": resp["contexts"],
            "selected_chunk_ids": [c["chunk_id"] for c in debug_chunks],
            "debug_stages": debug_stages,
            "ground_truth": item["ground_truth"],
            "expected_sources": item.get("expected_sources", []),
            "category": item["category"],
            "split": item.get("split"),
            "facet": item.get("facet"),
            "topic": item.get("topic"),
            "abstained": resp["abstained"],
            "retrieval_target_present": retrieval_target_present,
            "profile": policy.name,
            "model": model_choice["model"],
            "generator_model": model_choice["model"],
            "model_choice": model_choice,
            "model_choice_reason": model_choice["reason"],
            "evidence": resp.get("evidence"),
            "corrective_retrieval": resp.get("corrective_retrieval", {}),
            "query_decomposition": policy.query_decomposition_enabled,
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
        (
            captured_candidate_count,
            stage_candidate_counts,
            stage_candidate_counts_by_query_variant,
        ) = candidate_count_metadata(trace_record.get("candidate_stages", []))
        if holdout:
            holdout_operational.append(
                {
                    "candidate_count": captured_candidate_count,
                    "retrieval_latency_ms": float(
                        trace_record.get("retrieval_latency_ms", 0.0)
                    ),
                }
            )
        else:
            append_completed_row(
                retrieval_trace_path,
                eval_id,
                trace_lines,
                retrieval_latency_ms=trace_record.get("retrieval_latency_ms", 0.0),
                abstained=bool(resp.get("abstained")),
                category=item.get("category"),
                target_record=target_record,
                candidate_count=captured_candidate_count,
                stage_candidate_counts=stage_candidate_counts,
                stage_candidate_counts_by_query_variant=(
                    stage_candidate_counts_by_query_variant
                ),
                stage_timings_ms=trace_record.get("retrieval_stage_timings_ms", {}),
            )
        if holdout:
            print(f"[{i}/{total}]", flush=True)
        else:
            flag = "ABSTAIN" if row["abstained"] else "answered"
            print(f"[{i}/{total}] {row['category']:12} {flag:8} {elapsed:6.2f}s", flush=True)

    if holdout:
        artifacts.write_json(
            retrieval_summary_path,
            {
                "available": True,
                "holdout": True,
                "operational": {
                    "rows": len(holdout_operational),
                    "candidate_count_mean": (
                        round(
                            statistics.mean(
                                row["candidate_count"] for row in holdout_operational
                            ),
                            4,
                        )
                        if holdout_operational
                        else None
                    ),
                    "retrieval_latency_ms_mean": (
                        round(
                            statistics.mean(
                                row["retrieval_latency_ms"] for row in holdout_operational
                            ),
                            4,
                        )
                        if holdout_operational
                        else None
                    ),
                },
            },
        )
    else:
        save_retrieval_summary(
            retrieval_trace_path,
            retrieval_summary_path,
            holdout=False,
        )
    return results


def run_eval_set(splits: tuple[str, ...] = ("regression", "dev")) -> tuple[list[dict], Path, str]:
    from app.observability.logger import configure_logging

    configure_logging()

    from app.evals.dataset import load_eval_dataset

    dataset = load_eval_dataset(settings.eval_dataset_path, splits=splits)
    holdout = "holdout" in splits
    policy = resolve_policy().policy

    started_at = datetime.now().astimezone()
    model_slug = policy.generator_model.replace(":", "-").replace("/", "-")
    run_tag = artifacts.make_run_tag(model_slug, settings.eval_run_label, started_at)
    paths = artifacts.create_run_paths(run_tag, started_at)
    out_path = paths.run

    # Print the effective config BEFORE warmup: warmup already exercises the reranker
    # (a remote backend spends money on it), and a stale .env has silently confounded a
    # run before (dense_top_k=10 during the Haiku A/B). Eyeball this, then let it spend.
    print("Active config:")
    print(json.dumps(_active_config(), indent=2), flush=True)

    run_answer("warmup", trace=False)  # prime reranker + Ollama so row 1 isn't cold-start inflated

    results = run_rows(dataset, out_path, holdout=holdout)

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
        "profile": policy.name,
        "model": policy.generator_model,
        "generator_model": policy.generator_model,
        "model_slug": model_slug,
        "label": settings.eval_run_label,
        "question_count": len(results),
        "scored_count": None,
        "git_sha": _git_sha(),
        "active_config": _active_config(),
        "splits": list(splits),
        "holdout": holdout,
    }
    artifacts.save_meta(run_tag, meta)
    artifacts.update_manifest(run_tag, meta=meta)
    artifacts.write_latest(run_tag)

    return results, out_path, run_tag
