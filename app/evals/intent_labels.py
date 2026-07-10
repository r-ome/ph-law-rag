from collections import Counter
from pathlib import Path
from typing import Literal

from app.config import settings
from app.retriever.intent_router import INTENTS

IntentLabel = Literal[
    "default",
    "citation_lookup",
    "list_or_rule_synthesis",
    "amendment_or_current_law",
    "out_of_scope",
]

VALID_INTENTS: set[str] = set(INTENTS)


def load_intent_labels(
    dataset_path: str | Path | None = None,
) -> dict[str, IntentLabel]:
    """Return the v2 dataset's intent labels keyed by exact question text."""
    return {
        row["question"]: row["intent"]
        for row in _load_rows(dataset_path)
    }


def load_intent_labels_by_id(
    dataset_path: str | Path | None = None,
) -> dict[str, IntentLabel]:
    """Return the v2 dataset's intent labels keyed by stable eval ID."""
    return {
        row["id"]: row["intent"]
        for row in _load_rows(dataset_path)
    }


def _load_rows(dataset_path: str | Path | None) -> list[dict]:
    from app.evals.dataset import load_eval_dataset

    # Include holdout here: this is dataset metadata, not a run-selection helper.
    rows = load_eval_dataset(
        dataset_path or settings.eval_dataset_path,
        splits=("regression", "dev", "holdout"),
    )
    if not rows:
        raise ValueError("eval dataset contains no rows")
    labels = [row["intent"] for row in rows]
    invalid = sorted(set(labels) - VALID_INTENTS)
    if invalid:
        raise ValueError(f"invalid intent(s): {', '.join(invalid)}")
    return rows


def intent_counts(labels: dict[str, IntentLabel] | None = None) -> dict[str, int]:
    counts = Counter((labels or load_intent_labels()).values())
    return {intent: counts.get(intent, 0) for intent in sorted(VALID_INTENTS)}
