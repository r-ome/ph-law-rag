import json
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


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
    return rows


def _questions(rows: list[dict], path: Path) -> list[str]:
    questions: list[str] = []
    for index, row in enumerate(rows, start=1):
        question = row.get("question")
        if not isinstance(question, str) or not question:
            raise ValueError(f"{path}:{index}: missing non-empty question")
        questions.append(question)
    return questions


def _assert_unique(values: list[str], label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        sample = "; ".join(duplicates[:3])
        raise ValueError(f"duplicate {label}: {sample}")


def load_intent_labels(
    dataset_path: str | Path | None = None,
    labels_path: str | Path | None = None,
) -> dict[str, IntentLabel]:
    dataset = Path(dataset_path or settings.eval_dataset_path)
    labels = Path(labels_path or settings.eval_intent_labels_path)

    dataset_questions = _questions(_read_jsonl(dataset), dataset)
    label_rows = _read_jsonl(labels)

    _assert_unique(dataset_questions, "dataset question")
    label_questions = _questions(label_rows, labels)
    _assert_unique(label_questions, "intent label question")

    by_question: dict[str, IntentLabel] = {}
    for index, row in enumerate(label_rows, start=1):
        intent = row.get("intent")
        if intent not in VALID_INTENTS:
            raise ValueError(f"{labels}:{index}: invalid intent {intent!r}")
        by_question[row["question"]] = intent

    dataset_set = set(dataset_questions)
    label_set = set(by_question)
    missing = sorted(dataset_set - label_set)
    extra = sorted(label_set - dataset_set)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing labels for {len(missing)} question(s): {missing[:3]}")
        if extra:
            parts.append(f"extra labels for {len(extra)} question(s): {extra[:3]}")
        raise ValueError("; ".join(parts))

    return by_question


def intent_counts(labels: dict[str, IntentLabel] | None = None) -> dict[str, int]:
    counts = Counter((labels or load_intent_labels()).values())
    return {intent: counts.get(intent, 0) for intent in sorted(VALID_INTENTS)}
