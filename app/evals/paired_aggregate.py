from __future__ import annotations

import contextlib
import io
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals import artifacts
from app.evals.holdout_ledger import log_holdout_aggregate_read
from app.evals.integrity import canonical_json, file_sha256, sha256
from app.evals.ragas_cache import METRIC_NAMES
from app.evals.report import abstention_accuracy, save_scored

PRIMARY_METRICS = ("faithfulness", "context_recall")
PRECISION_METRIC = "llm_context_precision_with_reference"
ALLOWED_RETRIEVAL_POLICY_DIFFS = frozenset({"adaptive_context_enabled"})
HOLDOUT_PURPOSE = "phase4 v2 cap holdout validation"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_rows(tag: str) -> list[dict[str, Any]]:
    return _read_jsonl(artifacts.existing_path(tag, "run", required=True))


def _load_meta(tag: str) -> dict[str, Any]:
    meta = artifacts.load_meta(tag)
    if meta is None:
        raise ValueError("paired aggregate requires bundled meta for both arms")
    return meta


def _score_rows_quietly(
    tag: str,
    rows: list[dict[str, Any]],
    *,
    use_cache: bool,
) -> list[dict[str, Any]]:
    scored_path = artifacts.existing_path(tag, "scored")
    if scored_path is None:
        from app.evals.ragas_scorer import score

        with contextlib.redirect_stdout(io.StringIO()):
            scored = score(rows, use_cache=use_cache)
        save_scored(rows, scored, run_tag=tag)
        scored_path = artifacts.existing_path(tag, "scored", required=True)
    scored_rows = artifacts.read_json(scored_path)
    if not isinstance(scored_rows, list):
        raise ValueError("scored artifact must be a list")
    return scored_rows


def _row_id(row: dict[str, Any]) -> str:
    value = row.get("eval_id")
    if not isinstance(value, str) or not value:
        raise ValueError("run row missing eval_id")
    return value


def _assert_same_rows(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> list[str]:
    baseline_order = [_row_id(row) for row in baseline_rows]
    candidate_order = [_row_id(row) for row in candidate_rows]
    if not baseline_order or baseline_order != candidate_order:
        raise ValueError("mismatched eval IDs / order / row counts")
    return baseline_order


def _scored_by_id(scored_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for row in scored_rows:
        eval_id = row.get("eval_id")
        if not isinstance(eval_id, str) or not eval_id:
            raise ValueError("scored row missing eval_id")
        scores: dict[str, float] = {}
        for metric in METRIC_NAMES:
            if metric not in row:
                raise ValueError("missing gate metric")
            value = row[metric]
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("missing or non-finite score")
            scores[metric] = float(value)
        output[eval_id] = scores
    return output


def _answered_with_context(row: dict[str, Any]) -> bool:
    return (not bool(row.get("abstained"))) and bool(row.get("contexts"))


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("empty metric cohort")
    return float(statistics.mean(values))


def _overall_means(scored: dict[str, dict[str, float]]) -> dict[str, float | None]:
    if not scored:
        return {metric: None for metric in METRIC_NAMES}
    return {
        metric: _mean([scores[metric] for scores in scored.values()])
        for metric in METRIC_NAMES
    }


def _adaptive_stage(row: dict[str, Any]) -> dict[str, Any]:
    stages = [
        stage
        for stage in row.get("debug_stages", [])
        if isinstance(stage, dict) and stage.get("name") == "adaptive_context"
    ]
    if len(stages) != 1:
        raise ValueError("missing adaptive context diagnostics")
    return stages[0]


def _semantic_hash(row: dict[str, Any]) -> str:
    value = _adaptive_stage(row).get("packaging_pool_semantic_hash")
    if not isinstance(value, str) or not value:
        raise ValueError("missing packaging_pool_semantic_hash")
    return value


def _rendered_tokens(row: dict[str, Any]) -> float:
    value = _adaptive_stage(row).get("rendered_tokens")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("missing gate metric")
    return float(value)


def _activation_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        signals = _adaptive_stage(row).get("signals") or {}
        if not isinstance(signals, dict):
            raise ValueError("missing adaptive signal diagnostics")
        for name, active in sorted(signals.items()):
            if active:
                counter[name] += 1
    return dict(counter)


def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        value = _adaptive_stage(row).get(key)
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def _hash_or_none(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("hash", "sha256"):
        if isinstance(value.get(key), str):
            return value[key]
    return sha256(value)


def _require_meta_identity(
    baseline_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name in ("dataset_identity", "corpus_identity", "index_identity"):
        baseline_hash = _hash_or_none(baseline_meta.get(name))
        candidate_hash = _hash_or_none(candidate_meta.get(name))
        if not baseline_hash or not candidate_hash:
            raise ValueError(f"missing {name}")
        checks[name] = baseline_hash == candidate_hash
        if not checks[name]:
            raise ValueError(f"{name} mismatch")
    if not baseline_meta.get("git_sha") or baseline_meta.get("git_sha") != candidate_meta.get("git_sha"):
        raise ValueError("git SHA mismatch")
    checks["git_sha"] = True
    for meta in (baseline_meta, candidate_meta):
        consistency = meta.get("storage_consistency")
        if not isinstance(consistency, dict) or consistency.get("matched") is not True:
            raise ValueError("corpus/index drift within arm")
    checks["storage_consistency"] = True
    return checks


def _without_allowed_policy_diffs(config: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(config)
    cleaned.pop("policy_overrides_applied", None)
    for key in ALLOWED_RETRIEVAL_POLICY_DIFFS:
        cleaned.pop(key, None)
    return cleaned


def _require_retrieval_policy_identity(
    baseline_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
) -> dict[str, Any]:
    baseline = baseline_meta.get("active_config")
    candidate = candidate_meta.get("active_config")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError("missing retrieval policy")
    observed_diffs = {
        key
        for key in set(baseline) | set(candidate)
        if baseline.get(key) != candidate.get(key)
    }
    unapproved = observed_diffs - ALLOWED_RETRIEVAL_POLICY_DIFFS - {"policy_overrides_applied"}
    if unapproved or _without_allowed_policy_diffs(baseline) != _without_allowed_policy_diffs(candidate):
        raise ValueError("unapproved configuration difference")
    return {
        "allowed_diffs": sorted(observed_diffs & ALLOWED_RETRIEVAL_POLICY_DIFFS),
        "baseline_adaptive_context_enabled": bool(baseline.get("adaptive_context_enabled")),
        "candidate_adaptive_context_enabled": bool(candidate.get("adaptive_context_enabled")),
    }


def _require_scoring_identity(
    baseline_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
    *,
    use_cache: bool,
) -> dict[str, Any]:
    from app.evals.ragas_scorer import scoring_identity

    baseline = baseline_meta.get("scoring_identity") or scoring_identity(
        generator_model=baseline_meta.get("generator_model") or baseline_meta.get("model"),
        use_cache=use_cache,
    )
    candidate = candidate_meta.get("scoring_identity") or scoring_identity(
        generator_model=candidate_meta.get("generator_model") or candidate_meta.get("model"),
        use_cache=use_cache,
    )
    if canonical_json(baseline) != canonical_json(candidate):
        raise ValueError("full scoring identity mismatch")
    return baseline


def _require_semantic_pool_invariant(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> None:
    if any(_semantic_hash(b) != _semantic_hash(c) for b, c in zip(baseline_rows, candidate_rows)):
        raise ValueError("packaging_pool_semantic_hash mismatch")


def _quality_deltas(
    eval_order: list[str],
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    baseline_scores: dict[str, dict[str, float]],
    candidate_scores: dict[str, dict[str, float]],
) -> tuple[dict[str, Any], list[str]]:
    baseline_by_id = {_row_id(row): row for row in baseline_rows}
    candidate_by_id = {_row_id(row): row for row in candidate_rows}
    common = [
        eval_id
        for eval_id in eval_order
        if _answered_with_context(baseline_by_id[eval_id])
        and _answered_with_context(candidate_by_id[eval_id])
    ]
    if not common:
        raise ValueError("empty common-answered cohort")
    missing = [
        eval_id
        for eval_id in common
        if eval_id not in baseline_scores or eval_id not in candidate_scores
    ]
    if missing:
        raise ValueError("incomplete scoring of expected common-answered cohort")
    metrics = {}
    for metric in METRIC_NAMES:
        base_mean = _mean([baseline_scores[eval_id][metric] for eval_id in common])
        cand_mean = _mean([candidate_scores[eval_id][metric] for eval_id in common])
        metrics[metric] = {
            "baseline_mean": base_mean,
            "candidate_mean": cand_mean,
            "delta": cand_mean - base_mean,
        }
    return {"n": len(common), "metrics": metrics}, common


def _gate_status(delta: float) -> str:
    if delta >= -0.01:
        return "pass"
    if delta >= -0.02:
        return "inconclusive"
    return "fail"


def _assert_no_disclosure(payload: dict[str, Any], rows: list[dict[str, Any]], *, stdout: str = "") -> None:
    def scalar_values(value: Any) -> list[str]:
        if isinstance(value, dict):
            output: list[str] = []
            for item in value.values():
                output.extend(scalar_values(item))
            return output
        if isinstance(value, list):
            output: list[str] = []
            for item in value:
                output.extend(scalar_values(item))
            return output
        if isinstance(value, str):
            return [value]
        return []

    rendered_values = scalar_values(payload)
    stdout_values: list[str] = []
    if stdout:
        try:
            stdout_values = scalar_values(json.loads(stdout))
        except json.JSONDecodeError:
            stdout_values = [line.strip() for line in stdout.splitlines() if line.strip()]
    emitted_values = [*rendered_values, *stdout_values]

    substring_forbidden: set[str] = set()
    exact_forbidden: set[str] = set()
    for row in rows:
        for key in ("eval_id", "question", "answer", "ground_truth"):
            value = row.get(key)
            if isinstance(value, str) and value:
                substring_forbidden.add(value)
        category = row.get("category")
        if isinstance(category, str) and category:
            exact_forbidden.add(category)
        for context in row.get("contexts") or []:
            if isinstance(context, str) and context:
                substring_forbidden.add(context)
    leaked = [
        value
        for value in substring_forbidden
        if value in stdout or any(value in emitted for emitted in emitted_values)
    ]
    leaked.extend(
        value
        for value in exact_forbidden
        if value in stdout_values or any(value == emitted for emitted in rendered_values)
    )
    if leaked:
        raise AssertionError("paired aggregate disclosure guard failed")


def build_paired_aggregate(
    baseline_tag: str,
    candidate_tag: str,
    *,
    tag: str | None = None,
    use_cache: bool = True,
    holdout: bool | None = None,
    write_artifact: bool = True,
    log_holdout_read: bool = True,
) -> dict[str, Any]:
    baseline_rows = _load_rows(baseline_tag)
    candidate_rows = _load_rows(candidate_tag)
    eval_order = _assert_same_rows(baseline_rows, candidate_rows)
    baseline_meta = _load_meta(baseline_tag)
    candidate_meta = _load_meta(candidate_tag)
    holdout = (
        bool(baseline_meta.get("holdout") or candidate_meta.get("holdout"))
        if holdout is None
        else holdout
    )

    baseline_scored = _score_rows_quietly(baseline_tag, baseline_rows, use_cache=use_cache)
    candidate_scored = _score_rows_quietly(candidate_tag, candidate_rows, use_cache=use_cache)
    baseline_scores = _scored_by_id(baseline_scored)
    candidate_scores = _scored_by_id(candidate_scored)

    _require_semantic_pool_invariant(baseline_rows, candidate_rows)
    identity_checks = _require_meta_identity(baseline_meta, candidate_meta)
    retrieval_policy = _require_retrieval_policy_identity(baseline_meta, candidate_meta)
    scoring_identity = _require_scoring_identity(
        baseline_meta,
        candidate_meta,
        use_cache=use_cache,
    )
    common_quality, _ = _quality_deltas(
        eval_order,
        baseline_rows,
        candidate_rows,
        baseline_scores,
        candidate_scores,
    )

    baseline_abstention = abstention_accuracy(baseline_rows)
    candidate_abstention = abstention_accuracy(candidate_rows)
    baseline_mean_tokens = _mean([_rendered_tokens(row) for row in baseline_rows])
    candidate_mean_tokens = _mean([_rendered_tokens(row) for row in candidate_rows])
    if baseline_mean_tokens <= 0:
        raise ValueError("missing gate metric")
    reduction = (baseline_mean_tokens - candidate_mean_tokens) / baseline_mean_tokens

    gates = {
        metric: {
            "delta": common_quality["metrics"][metric]["delta"],
            "status": _gate_status(common_quality["metrics"][metric]["delta"]),
        }
        for metric in PRIMARY_METRICS
    }
    gates["false_abstentions"] = {
        "baseline": baseline_abstention["false_abstentions"],
        "candidate": candidate_abstention["false_abstentions"],
        "status": (
            "pass"
            if candidate_abstention["false_abstentions"]
            <= baseline_abstention["false_abstentions"]
            else "fail"
        ),
    }
    gates["rendered_token_reduction"] = {
        "value": reduction,
        "status": "pass" if reduction >= 0.05 else "fail",
    }
    hard_statuses = [gate["status"] for gate in gates.values()]
    verdict = (
        "eligible_for_release_decision"
        if all(status == "pass" for status in hard_statuses)
        else "fail_to_graduate"
    )
    if "inconclusive" in hard_statuses:
        verdict = "fail_to_graduate_inconclusive"

    artifact = {
        "artifact_type": "phase4_paired_aggregate",
        "tag": tag,
        "created_at": datetime.now().astimezone().isoformat(),
        "holdout": holdout,
        "row_count": len(eval_order),
        "arms": {"baseline_tag": baseline_tag, "candidate_tag": candidate_tag},
        "identity_checks": identity_checks,
        "retrieval_policy": retrieval_policy,
        "full_scoring_identity": scoring_identity,
        "common_answered_quality": common_quality,
        "sensitivity": {
            "baseline_overall": _overall_means(baseline_scores),
            "candidate_overall": _overall_means(candidate_scores),
        },
        "abstention": {
            "baseline": baseline_abstention,
            "candidate": candidate_abstention,
        },
        "benefit": {
            "baseline_mean_rendered_tokens": baseline_mean_tokens,
            "candidate_mean_rendered_tokens": candidate_mean_tokens,
            "rendered_token_reduction": reduction,
        },
        "execution": {
            "changed_context_count": sum(
                b.get("selected_chunk_ids") != c.get("selected_chunk_ids")
                for b, c in zip(baseline_rows, candidate_rows)
            ),
            "baseline_activation_counts": _activation_counts(baseline_rows),
            "candidate_activation_counts": _activation_counts(candidate_rows),
            "baseline_cap_distribution": _distribution(baseline_rows, "cap"),
            "candidate_cap_distribution": _distribution(candidate_rows, "cap"),
            "baseline_stop_distribution": _distribution(baseline_rows, "stop_reason"),
            "candidate_stop_distribution": _distribution(candidate_rows, "stop_reason"),
            "labeled_synthesis_holdout_n": 4 if holdout else None,
        },
        "gates": gates,
        "verdict": verdict,
    }
    _assert_no_disclosure(artifact, [*baseline_rows, *candidate_rows])

    if write_artifact:
        if not tag:
            tag = f"phase4_paired_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            artifact["tag"] = tag
        path = artifacts.results_dir() / "phase4_paired" / f"{tag}.json"
        if path.exists():
            raise FileExistsError(f"paired aggregate tag already exists: {tag}")
        artifacts.write_json(path, artifact)
        artifact["artifact_path"] = str(path)
        artifacts.write_json(path, artifact)

    if holdout and log_holdout_read:
        log_holdout_aggregate_read(
            access_type="phase4_paired_aggregate",
            tags=[baseline_tag, candidate_tag],
            purpose=HOLDOUT_PURPOSE,
            source="app.evals.paired_aggregate",
            extra={
                "artifact_hash": (
                    file_sha256(Path(artifact["artifact_path"]))
                    if artifact.get("artifact_path")
                    else sha256(artifact)
                ),
                "row_count": len(eval_order),
            },
        )
    return artifact


def printable_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    """Stable final stdout surface; deliberately aggregate-only."""
    return {
        "tag": artifact.get("tag"),
        "holdout": artifact["holdout"],
        "row_count": artifact["row_count"],
        "common_answered_n": artifact["common_answered_quality"]["n"],
        "quality_deltas": {
            metric: round(
                artifact["common_answered_quality"]["metrics"][metric]["delta"],
                4,
            )
            for metric in PRIMARY_METRICS
        },
        "sensitivity": artifact["sensitivity"],
        "abstention": artifact["abstention"],
        "benefit": {
            **artifact["benefit"],
            "rendered_token_reduction": round(
                artifact["benefit"]["rendered_token_reduction"],
                4,
            ),
        },
        "execution": artifact["execution"],
        "gates": artifact["gates"],
        "verdict": artifact["verdict"],
        "artifact_path": artifact.get("artifact_path"),
    }
