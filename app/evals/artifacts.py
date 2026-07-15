import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.config import settings

ArtifactKind = Literal[
    "run",
    "scored",
    "summary",
    "meta",
    "retrieval_trace",
    "retrieval_summary",
]


@dataclass(frozen=True)
class EvalArtifactPaths:
    tag: str
    run_dir: Path | None
    run: Path
    scored: Path
    summary: Path
    meta: Path | None
    retrieval_trace: Path
    retrieval_summary: Path
    layout: Literal["bundled", "legacy"]


def results_dir() -> Path:
    return Path(settings.eval_results_dir)


def make_run_tag(model_slug: str, label: str, started_at: datetime) -> str:
    label_part = f"_{label}" if label else ""
    return f"{model_slug}{label_part}_{started_at.strftime('%Y%m%d_%H%M%S')}"


def create_run_paths(tag: str, started_at: datetime) -> EvalArtifactPaths:
    run_dir = results_dir() / "runs" / started_at.strftime("%Y-%m-%d") / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    return EvalArtifactPaths(
        tag=tag,
        run_dir=run_dir,
        run=run_dir / "run.jsonl",
        scored=run_dir / "scored.json",
        summary=run_dir / "summary.json",
        meta=run_dir / "meta.json",
        retrieval_trace=run_dir / "retrieval_trace.jsonl",
        retrieval_summary=run_dir / "retrieval_summary.json",
        layout="bundled",
    )


def tag_from_run_path(run_path: str | Path) -> str:
    path = Path(run_path)
    if path.name == "run.jsonl":
        return path.parent.name
    if path.name.startswith("run_") and path.suffix == ".jsonl":
        return path.name.removeprefix("run_").removesuffix(".jsonl")
    raise ValueError(f"cannot infer eval run tag from {path}")


def _bundled_run_dir(tag: str) -> Path | None:
    runs_dir = results_dir() / "runs"
    if not runs_dir.exists():
        return None

    matches: list[Path] = []
    for date_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        candidate = date_dir / tag
        if candidate.is_dir():
            matches.append(candidate)
    return matches[-1] if matches else None


def paths_for_tag(tag: str) -> EvalArtifactPaths:
    run_dir = _bundled_run_dir(tag)
    if run_dir is not None:
        return EvalArtifactPaths(
            tag=tag,
            run_dir=run_dir,
            run=run_dir / "run.jsonl",
            scored=run_dir / "scored.json",
            summary=run_dir / "summary.json",
            meta=run_dir / "meta.json",
            retrieval_trace=run_dir / "retrieval_trace.jsonl",
            retrieval_summary=run_dir / "retrieval_summary.json",
            layout="bundled",
        )

    base = results_dir()
    return EvalArtifactPaths(
        tag=tag,
        run_dir=None,
        run=base / f"run_{tag}.jsonl",
        scored=base / f"scored_{tag}.json",
        summary=base / f"summary_{tag}.json",
        meta=None,
        retrieval_trace=base / f"retrieval_trace_{tag}.jsonl",
        retrieval_summary=base / f"retrieval_summary_{tag}.json",
        layout="legacy",
    )


def existing_path(tag: str, kind: ArtifactKind, *, required: bool = False) -> Path | None:
    paths = paths_for_tag(tag)
    path = getattr(paths, kind)
    if path is not None and path.exists():
        return path
    if required:
        raise FileNotFoundError(path)
    return None


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(tag: str, meta: dict[str, Any]) -> None:
    path = paths_for_tag(tag).meta
    if path is None:
        return
    write_json(path, meta)


def load_meta(tag: str) -> dict[str, Any] | None:
    path = existing_path(tag, "meta")
    return read_json(path) if path else None


def _relative(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(results_dir()))
    except ValueError:
        return str(path)


def write_latest(tag: str) -> None:
    paths = paths_for_tag(tag)
    write_json(
        results_dir() / "latest.json",
        {
            "tag": tag,
            "layout": paths.layout,
            "run_dir": _relative(paths.run_dir),
            "run_path": _relative(paths.run),
            "summary_path": _relative(paths.summary) if paths.summary.exists() else None,
            "scored_path": _relative(paths.scored) if paths.scored.exists() else None,
            "retrieval_trace_path": (
                _relative(paths.retrieval_trace) if paths.retrieval_trace.exists() else None
            ),
            "retrieval_summary_path": (
                _relative(paths.retrieval_summary)
                if paths.retrieval_summary.exists()
                else None
            ),
        },
    )


def _read_manifest_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _count_jsonl(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _count_scored(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    data = read_json(path)
    return len(data) if isinstance(data, list) else None


def manifest_row(tag: str, meta: dict[str, Any] | None = None, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = paths_for_tag(tag)
    meta = meta or load_meta(tag) or {}
    summary_path = existing_path(tag, "summary")
    if summary is None and summary_path is not None:
        summary = read_json(summary_path)
    summary = summary or {}
    overall = summary.get("overall", {})
    abstention = summary.get("abstention", {})

    run_path = paths.run if paths.run.exists() else None
    scored_path = paths.scored if paths.scored.exists() else None

    return {
        "tag": tag,
        "date": meta.get("date") or (meta.get("started_at", "")[:10] or None),
        "model": meta.get("model"),
        "label": meta.get("label", ""),
        "questions": meta.get("question_count") or _count_jsonl(run_path),
        "scored": meta.get("scored_count") if meta.get("scored_count") is not None else _count_scored(scored_path),
        "holdout": bool(meta.get("holdout")),
        "git_sha": meta.get("git_sha"),
        "abstention_accuracy": abstention.get("accuracy"),
        "faithfulness": overall.get("faithfulness"),
        "answer_relevancy": overall.get("answer_relevancy"),
        "context_precision": overall.get("llm_context_precision_with_reference"),
        "context_recall": overall.get("context_recall"),
        "layout": paths.layout,
        "run_path": _relative(run_path),
        "summary_path": _relative(paths.summary) if paths.summary.exists() else None,
        "scored_path": _relative(paths.scored) if paths.scored.exists() else None,
    }


def update_manifest(tag: str, meta: dict[str, Any] | None = None, summary: dict[str, Any] | None = None) -> None:
    path = results_dir() / "manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = manifest_row(tag, meta=meta, summary=summary)
    rows = _read_manifest_rows(path)

    replaced = False
    for i, existing in enumerate(rows):
        if existing.get("tag") == tag:
            rows[i] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)

    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in rows),
        encoding="utf-8",
    )
