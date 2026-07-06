"""Organize legacy eval artifacts into the bundled eval_results layout.

Default mode is dry-run. Use --apply to move files.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.evals import artifacts


RUN_RE = re.compile(r"^run_(.+)\.jsonl$")
SCORED_RE = re.compile(r"^scored_(.+)\.json$")
SUMMARY_RE = re.compile(r"^summary_(.+)\.json$")
DATE_RE = re.compile(r"(20\d{6})")


@dataclass
class Bundle:
    tag: str
    files: dict[str, Path] = field(default_factory=dict)


def _loads_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except Exception:
        return []
    return rows


def _date_from_tag(tag: str) -> str:
    for match in reversed(DATE_RE.findall(tag)):
        try:
            return datetime.strptime(match, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            continue
    return "unknown-date"


def _group_bundles(root: Path) -> dict[str, Bundle]:
    bundles: dict[str, Bundle] = {}
    for path in root.iterdir():
        if not path.is_file():
            continue

        kind = tag = None
        if match := RUN_RE.match(path.name):
            kind, tag = "run", match.group(1)
        elif path.name == "scored_latest.json":
            continue
        elif match := SCORED_RE.match(path.name):
            kind, tag = "scored", match.group(1)
        elif match := SUMMARY_RE.match(path.name):
            kind, tag = "summary", match.group(1)

        if kind and tag:
            bundles.setdefault(tag, Bundle(tag=tag)).files[kind] = path
    return bundles


def _meta_for(bundle: Bundle) -> dict[str, Any]:
    run_rows = _load_jsonl(bundle.files["run"]) if "run" in bundle.files else []
    scored_rows = _loads_json(bundle.files["scored"]) if "scored" in bundle.files else None
    first = run_rows[0] if run_rows else {}
    query_decomp = first.get("query_decomposition")

    active_config = {}
    if query_decomp is not None:
        active_config["query_decomposition_enabled"] = query_decomp

    return {
        "tag": bundle.tag,
        "date": _date_from_tag(bundle.tag),
        "started_at": None,
        "completed_at": None,
        "model": first.get("model"),
        "model_slug": None,
        "label": "",
        "question_count": len(run_rows) if run_rows else None,
        "scored_count": len(scored_rows) if isinstance(scored_rows, list) else None,
        "git_sha": None,
        "active_config": active_config,
        "migrated_from_legacy": True,
        "source_files": {kind: path.name for kind, path in sorted(bundle.files.items())},
    }


def _summary_for(bundle: Bundle) -> dict[str, Any] | None:
    if "summary" not in bundle.files:
        return None
    data = _loads_json(bundle.files["summary"])
    return data if isinstance(data, dict) else None


def _move(src: Path, dst: Path, *, apply: bool, moves: list[str]) -> None:
    moves.append(f"{src} -> {dst}")
    if not apply:
        return
    if dst.exists():
        raise FileExistsError(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _write_json(path: Path, payload: dict[str, Any], *, apply: bool, writes: list[str]) -> None:
    writes.append(str(path))
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _latest_tag(bundles: dict[str, Bundle]) -> str | None:
    candidates = []
    for bundle in bundles.values():
        mtimes = [path.stat().st_mtime for path in bundle.files.values() if path.exists()]
        if mtimes:
            candidates.append((max(mtimes), bundle.tag))
    return max(candidates)[1] if candidates else None


def migrate(root: Path, *, apply: bool) -> dict[str, Any]:
    moves: list[str] = []
    writes: list[str] = []
    bundles = _group_bundles(root)
    latest_tag = _latest_tag(bundles)
    metas = {tag: _meta_for(bundle) for tag, bundle in bundles.items()}
    summaries = {tag: _summary_for(bundle) for tag, bundle in bundles.items()}

    for tag in sorted(bundles):
        bundle = bundles[tag]
        run_dir = root / "runs" / _date_from_tag(tag) / tag
        targets = {
            "run": run_dir / "run.jsonl",
            "scored": run_dir / "scored.json",
            "summary": run_dir / "summary.json",
        }
        for kind in ("run", "scored", "summary"):
            if kind in bundle.files:
                _move(bundle.files[kind], targets[kind], apply=apply, moves=moves)

        _write_json(run_dir / "meta.json", metas[tag], apply=apply, writes=writes)

    for path in sorted(root.glob("diff_*")):
        if path.is_file():
            _move(path, root / "diffs" / path.name, apply=apply, moves=moves)

    for path in sorted(root.glob("trace_*")):
        if path.is_file():
            _move(path, root / "traces" / path.name, apply=apply, moves=moves)

    for path in sorted(root.glob("*_run.log")):
        if path.is_file():
            _move(path, root / "logs" / path.name.lstrip("_"), apply=apply, moves=moves)

    scratch = root / "recomputed_contexts_k30qwen.json"
    if scratch.exists():
        _move(scratch, root / "scratch" / scratch.name, apply=apply, moves=moves)

    latest = root / "scored_latest.json"
    if latest.exists():
        _move(latest, root / "legacy" / latest.name, apply=apply, moves=moves)

    if apply:
        for tag in sorted(bundles):
            artifacts.update_manifest(tag, meta=metas[tag], summary=summaries[tag])
        if latest_tag:
            artifacts.write_latest(latest_tag)
        (root / "README.md").write_text(
            "# Eval Results\n\n"
            "- `runs/YYYY-MM-DD/<tag>/`: bundled run artifacts (`run.jsonl`, `meta.json`, `summary.json`, `scored.json`).\n"
            "- `manifest.jsonl`: one summary row per run.\n"
            "- `latest.json`: pointer to the latest run.\n"
            "- `diffs/`, `traces/`, `logs/`, `scratch/`: diagnostic artifacts.\n"
            "- `legacy/`: superseded compatibility files kept for reference.\n"
            "- `ragas_score_cache.sqlite`: left at the configured root path until a config-aware cache move is done.\n",
            encoding="utf-8",
        )
    else:
        writes.extend([str(root / "manifest.jsonl"), str(root / "latest.json"), str(root / "README.md")])

    return {
        "apply": apply,
        "bundles": len(bundles),
        "latest_tag": latest_tag,
        "moves": moves,
        "writes": writes,
    }


def repair_metadata(root: Path, *, apply: bool) -> dict[str, Any]:
    writes: list[str] = []
    bundles: dict[str, Bundle] = {}
    runs_root = root / "runs"
    if runs_root.exists():
        for run_dir in sorted(p for p in runs_root.glob("*/*") if p.is_dir()):
            bundle = Bundle(tag=run_dir.name)
            for kind, name in (("run", "run.jsonl"), ("scored", "scored.json"), ("summary", "summary.json")):
                path = run_dir / name
                if path.exists():
                    bundle.files[kind] = path
            bundles[bundle.tag] = bundle

    for tag in sorted(bundles):
        bundle = bundles[tag]
        meta = _meta_for(bundle)
        run_dir = bundle.files.get("run", next(iter(bundle.files.values()))).parent
        _write_json(run_dir / "meta.json", meta, apply=apply, writes=writes)
        if apply:
            artifacts.update_manifest(tag, meta=meta, summary=_summary_for(bundle))

    if apply:
        latest_path = root / "latest.json"
        if latest_path.exists():
            latest = _loads_json(latest_path)
            if isinstance(latest, dict) and latest.get("tag"):
                artifacts.write_latest(latest["tag"])

    return {
        "apply": apply,
        "bundles": len(bundles),
        "writes": writes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/eval_results")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair-metadata", action="store_true")
    args = parser.parse_args()

    if args.repair_metadata:
        result = repair_metadata(Path(args.root), apply=args.apply)
    else:
        result = migrate(Path(args.root), apply=args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
