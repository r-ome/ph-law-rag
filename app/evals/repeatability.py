import math
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals import artifacts
from app.evals.runner import load_dataset

CAVEAT = (
    "repeated judging measures variance, not correctness — a consistently wrong judge still looks stable."
)


def _metric_columns(df) -> list[str]:
    return [
        col
        for col in df.select_dtypes(include="number").columns
        if not str(col).startswith("__")
    ]


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[int(pos)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _select_panel(results: list[dict], row_ids: list[str], sample_size: int) -> list[dict]:
    scorable = [row for row in results if not row.get("abstained") and row.get("contexts")]
    if row_ids:
        wanted = set(row_ids)
        selected = [row for row in scorable if row.get("eval_id") in wanted or row.get("id") in wanted]
        missing = sorted(wanted - {row.get("eval_id") or row.get("id") for row in selected})
        if missing:
            raise ValueError(f"row id(s) not found or not scorable: {', '.join(missing)}")
        return selected
    return scorable[:sample_size]


def run_repeatability_panel(
    run_path: str | Path,
    *,
    repeats: int = 5,
    row_ids: list[str] | None = None,
    sample_size: int = 10,
    out: str | Path | None = None,
) -> tuple[dict[str, Any], Path]:
    if repeats < 2:
        raise ValueError("repeats must be at least 2")
    if sample_size < 1:
        raise ValueError("sample_size must be at least 1")

    from app.evals.ragas_scorer import judge_model, score
    from app.evals.runner import _git_sha

    results = load_dataset(str(run_path))
    if any(row.get("split") == "holdout" for row in results):
        raise ValueError("repeatability panel cannot use holdout run artifacts")
    panel = _select_panel(results, row_ids or [], sample_size)
    if not panel:
        raise ValueError("repeatability panel has no scorable rows")

    row_ids_out = [row.get("eval_id") or row.get("id") for row in panel]
    by_metric: dict[str, dict[str, list[float]]] = {}
    nan_counts: dict[str, int] = {}

    for _ in range(repeats):
        ragas_result, scorable = score(panel, use_cache=False)
        if ragas_result is None:
            raise ValueError("repeatability panel has no scorable rows")
        df = ragas_result.to_pandas()
        metric_cols = _metric_columns(df)
        if not by_metric:
            by_metric = {
                metric: {str(row_ids_out[i]): [] for i in range(len(scorable))}
                for metric in metric_cols
            }
            nan_counts = {metric: 0 for metric in metric_cols}
        for metric in metric_cols:
            for i, value in enumerate(df[metric].tolist()):
                finite = _finite(value)
                if finite is None:
                    nan_counts[metric] = nan_counts.get(metric, 0) + 1
                    continue
                by_metric.setdefault(metric, {}).setdefault(str(row_ids_out[i]), []).append(finite)

    metric_summaries: dict[str, dict[str, Any]] = {}
    for metric, rows in by_metric.items():
        ranges = [
            max(values) - min(values)
            for values in rows.values()
            if len(values) >= 2
        ]
        metric_summaries[metric] = {
            "median_within_row_range": _round(_percentile(ranges, 0.5)),
            "p90_within_row_range": _round(_percentile(ranges, 0.9)),
            "max_within_row_range": _round(max(ranges) if ranges else None),
            "nan_count": nan_counts.get(metric, 0),
            "rows_with_range": len(ranges),
        }

    run_tag = artifacts.tag_from_run_path(run_path)
    run_path_obj = Path(run_path)
    payload = {
        "tag": run_tag,
        "generated_at": datetime.now().astimezone().isoformat(),
        "run_path": str(run_path),
        "run_sha256": _sha256_file(run_path_obj),
        "panel_sha256": _sha256_json(panel),
        "rows": row_ids_out,
        "row_count": len(panel),
        "repeats": repeats,
        "git_sha": _git_sha(),
        "ragas_version": _package_version("ragas"),
        "judge_model": judge_model(),
        "ragas_judge_backend": settings.ragas_judge_backend,
        "ragas_embedding_model": settings.ragas_embedding_model,
        "scorer_identity": {
            "module": "app.evals.ragas_scorer",
            "function": "score",
            "metrics": [
                "Faithfulness",
                "ResponseRelevancy",
                "LLMContextPrecisionWithReference",
                "LLMContextRecall",
            ],
        },
        "cache": "bypassed",
        "metrics": metric_summaries,
        "caveat": CAVEAT,
    }

    if out is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = artifacts.results_dir() / "repeatability" / f"repeatability_{run_tag}_{stamp}.json"
    else:
        out_path = Path(out)
    artifacts.write_json(out_path, payload)
    return payload, out_path
