"""Schema and loading helpers for the versioned evaluation dataset."""

import json
from pathlib import Path
import re


SPLITS = frozenset({"regression", "dev", "holdout"})
FACETS = frozenset({
    "lookup", "definition", "exception", "threshold", "condition", "timing",
    "remedy", "effect", "penalty", "synthesis", "unknown", "not_applicable",
})
DIFFICULTIES = frozenset({"easy", "medium", "hard", "not_applicable"})

_CONTENT_KEYS = {"question", "ground_truth", "expected_sources", "category", "topic"}
_REQUIRED_KEYS = {
    "id", "split", *_CONTENT_KEYS, "intent", "facet", "difficulty", "provisions",
}
_ALLOWED_KEYS = _REQUIRED_KEYS


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: row must be an object")
            row["__line__"] = line_no
            row["__path__"] = str(path)
            rows.append(row)
    return rows


def _enabled_source_ids() -> set[str]:
    # Keep the config/YAML import lazy so merely importing eval helpers has no YAML cost.
    from app.config import load_allowed_sources

    return {source.source_id for source in load_allowed_sources()}


def _row_location(row: dict, index: int) -> str:
    if "__path__" in row:
        return f"{row['__path__']}:{row.get('__line__', index)}"
    return f"row {row.get('__line__', index)}"


def _require_string(row: dict, key: str, location: str) -> None:
    if not isinstance(row.get(key), str) or not row[key]:
        raise ValueError(f"{location}: missing non-empty {key}")


def validate_rows(rows: list[dict], enabled_source_ids: set[str]) -> None:
    """Validate v2 rows. Raises ``ValueError`` with a useful row location."""
    from app.retriever.intent_router import INTENTS

    ids: dict[str, str] = {}
    questions: dict[str, str] = {}
    for index, row in enumerate(rows, start=1):
        location = _row_location(row, index)
        keys = set(row) - {"__line__", "__path__"}
        missing = _REQUIRED_KEYS - keys
        extra = keys - _ALLOWED_KEYS
        if missing:
            raise ValueError(f"{location}: missing required key(s): {', '.join(sorted(missing))}")
        if extra:
            raise ValueError(f"{location}: unknown key(s): {', '.join(sorted(extra))}")

        for key in ("id", "question", "ground_truth", "category", "topic"):
            _require_string(row, key, location)
        if not re.fullmatch(r"eval_\d{3}", row["id"]):
            raise ValueError(f"{location}: id must use eval_NNN format")
        if row["id"] in ids:
            raise ValueError(f"{location}: duplicate eval id {row['id']!r} (first at {ids[row['id']]})")
        if row["question"] in questions:
            raise ValueError(f"{location}: duplicate dataset question (first at {questions[row['question']]})")
        if row["split"] not in SPLITS:
            raise ValueError(f"{location}: invalid split {row['split']!r}")
        if row["facet"] not in FACETS:
            raise ValueError(f"{location}: invalid facet {row['facet']!r}")
        if row["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"{location}: invalid difficulty {row['difficulty']!r}")
        if row["intent"] not in INTENTS:
            raise ValueError(f"{location}: invalid intent {row['intent']!r}")
        if not isinstance(row["expected_sources"], list) or not all(
            isinstance(source_id, str) for source_id in row["expected_sources"]
        ):
            raise ValueError(f"{location}: expected_sources must be a list of source IDs")

        provisions = row["provisions"]
        if not isinstance(provisions, list):
            raise ValueError(f"{location}: provisions must be a list")
        for provision in provisions:
            if not isinstance(provision, dict) or set(provision) != {"source_id", "cite"}:
                raise ValueError(f"{location}: each provision must contain only source_id and cite")
            if not isinstance(provision["source_id"], str) or not provision["source_id"]:
                raise ValueError(f"{location}: provision source_id must be non-empty")
            if not isinstance(provision["cite"], str) or not provision["cite"]:
                raise ValueError(f"{location}: provision cite must be non-empty")

        source_ids = [*row["expected_sources"], *(p["source_id"] for p in provisions)]
        unknown_source_ids = sorted(set(source_ids) - enabled_source_ids)
        if unknown_source_ids:
            raise ValueError(f"{location}: unknown enabled source_id(s): {', '.join(unknown_source_ids)}")
        if not provisions and row["facet"] != "not_applicable":
            raise ValueError(f"{location}: empty provisions require facet 'not_applicable'")
        if row["facet"] == "not_applicable" and provisions:
            raise ValueError(f"{location}: facet 'not_applicable' requires empty provisions")

        # not_applicable is reserved for abstention rows (the out-of-scope category);
        # facet, difficulty, provisions, and expected_sources travel together there.
        na_facet = row["facet"] == "not_applicable"
        if na_facet != (row["difficulty"] == "not_applicable"):
            raise ValueError(
                f"{location}: facet and difficulty must both be 'not_applicable' or neither"
            )
        if na_facet != (row["category"] == "out-of-scope"):
            raise ValueError(
                f"{location}: facet 'not_applicable' is reserved for category 'out-of-scope'"
            )
        if na_facet and row["expected_sources"]:
            raise ValueError(f"{location}: out-of-scope rows must have empty expected_sources")

        ids[row["id"]] = location
        questions[row["question"]] = location


def load_eval_dataset(
    path: str | Path | None = None,
    splits: tuple[str, ...] | None = None,
) -> list[dict]:
    """Load and validate v2 JSONL, defaulting to non-holdout splits."""
    if splits is not None:
        invalid = sorted(set(splits) - SPLITS)
        if invalid:
            raise ValueError(f"invalid requested split(s): {', '.join(invalid)}")
    else:
        splits = ("regression", "dev")

    if path is None:
        from app.config import settings

        path = settings.eval_dataset_path
    dataset_path = Path(path)
    rows = _read_jsonl(dataset_path)
    validate_rows(rows, _enabled_source_ids())
    selected = set(splits)
    return [
        {key: value for key, value in row.items() if key not in {"__line__", "__path__"}}
        for row in rows
        if row["split"] in selected
    ]
