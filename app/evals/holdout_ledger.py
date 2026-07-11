import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals import artifacts


def ledger_path() -> Path:
    return artifacts.results_dir() / "holdout_aggregate_reads.jsonl"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _row_count(tag: str) -> int | None:
    meta = artifacts.load_meta(tag) or {}
    if meta.get("question_count") is not None:
        return meta["question_count"]
    manifest = artifacts.manifest_row(tag)
    return manifest.get("questions")


def holdout_aggregate_metadata(tags: list[str]) -> dict[str, Any]:
    dataset_path = Path(settings.eval_dataset_path)
    manifest_path = artifacts.results_dir() / "manifest.jsonl"
    return {
        "holdout_row_counts": {tag: _row_count(tag) for tag in tags},
        "eval_dataset_sha256": _sha256(dataset_path),
        "eval_manifest_sha256": _sha256(manifest_path),
        "git_sha": _git_sha(),
    }


def log_holdout_aggregate_read(
    *,
    access_type: str,
    tags: list[str],
    purpose: str | None = None,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "access_type": access_type,
        "tags": tags,
        "purpose": purpose,
        "source": source,
        **holdout_aggregate_metadata(tags),
    }
    if extra:
        record["extra"] = extra
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path
