import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import SourceConfig, settings
from app.evals import artifacts


def _read_dataset_rows(path: str | Path | None = None) -> list[dict[str, Any]]:
    dataset_path = Path(path or settings.eval_dataset_path)
    if not dataset_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in dataset_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _amended_sources(source_id: str, by_id: dict[str, SourceConfig]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    stack = list(reversed(by_id.get(source_id).amends if by_id.get(source_id) else []))

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        source = by_id.get(current)
        if source:
            stack.extend(reversed(source.amends))

    return ordered


def _row_record(row: dict[str, Any], matched_sources: list[str]) -> dict[str, Any]:
    split = row.get("split")
    if split == "holdout":
        return {
            "id": row.get("id"),
            "split": "holdout",
            "holdout_redacted": True,
        }
    return {
        "id": row.get("id"),
        "split": split,
        "category": row.get("category"),
        "topic": row.get("topic"),
        "question": row.get("question"),
        "expected_sources": row.get("expected_sources") or [],
        "matched_sources": matched_sources,
    }


def build_source_review_report(
    *,
    sync_run_id: str,
    changed_sources: list[dict[str, str]],
    sources: list[SourceConfig],
    dataset_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_id = {source.source_id: source for source in sources}
    rows = _read_dataset_rows() if dataset_rows is None else dataset_rows
    changed: list[dict[str, Any]] = []
    affected_source_ids: set[str] = set()

    for item in changed_sources:
        source_id = item["source_id"]
        amended = _amended_sources(source_id, by_id)
        source_ids = [source_id, *amended]
        affected_source_ids.update(source_ids)
        changed.append({
            "source_id": source_id,
            "change_status": item["status"],
            "reason": "new source ingested" if item["status"] == "new" else "source content hash changed",
            "amends": amended,
            "matched_source_ids": source_ids,
        })

    affected_rows: list[dict[str, Any]] = []
    for row in rows:
        expected = row.get("expected_sources") or []
        if not isinstance(expected, list):
            continue
        matched = [source_id for source_id in expected if source_id in affected_source_ids]
        if matched:
            affected_rows.append(_row_record(row, matched))

    status = "ground_truth_review_required" if affected_rows else "no_eval_rows_affected"
    return {
        "sync_run_id": sync_run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "changed_sources": changed,
        "affected_row_count": len(affected_rows),
        "affected_rows": affected_rows,
    }


def write_source_review_report(
    *,
    sync_run_id: str,
    changed_sources: list[dict[str, str]],
    sources: list[SourceConfig],
) -> Path | None:
    if not changed_sources:
        return None

    report = build_source_review_report(
        sync_run_id=sync_run_id,
        changed_sources=changed_sources,
        sources=sources,
    )
    out_path = artifacts.results_dir() / "source_reviews" / f"{sync_run_id}.json"
    artifacts.write_json(out_path, report)
    return out_path
