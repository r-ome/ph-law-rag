from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.evals.intent_labels import load_intent_labels
from app.retriever.intent_router import (
    FEW_SHOTS,
    INTENTS,
    SYSTEM_PROMPT,
    parse_prediction,
    render_llm_prompts,
)

LLM_ARMS = {"mistral", "haiku", "qwen3", "gemma3"}
ALL_ARMS = ["mistral", "haiku", "nli", "qwen3", "gemma3"]

DEFAULT_MODELS = {
    "mistral": "mistral",
    "haiku": "claude-haiku-4-5",
    "nli": "cross-encoder/nli-deberta-v3-base",
    "qwen3": "qwen3:4b",
    "gemma3": "gemma3:4b",
}

DEFAULT_NLI_MARGIN_THRESHOLD = 0.15

SMOKE_QUESTIONS = [
    ("Can a person refuse to testify against himself in a criminal case?", "default"),
    ("What does Article 308 of the Revised Penal Code say?", "citation_lookup"),
    ("What are the requisites of a valid contract?", "list_or_rule_synthesis"),
    ("Did a newer law change the old statutory rape age threshold?", "amendment_or_current_law"),
    ("How much is the filing fee for a land registration case?", "out_of_scope"),
]

NLI_HYPOTHESES = {
    "default": "This question asks for ordinary legal information that can be answered by normal retrieval.",
    "citation_lookup": "This question asks what a specific article, section, statute, or legal citation says.",
    "list_or_rule_synthesis": "This question asks for a list, set of elements, requisites, grounds, rights, or rules.",
    "amendment_or_current_law": "This question asks which law is currently operative after an amendment, repeal, supersession, or later-enacted change.",
    "out_of_scope": "This question asks about a topic outside the indexed Philippine law corpus.",
}

def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def render_nli_prompt() -> str:
    return _json_dumps({"hypotheses": NLI_HYPOTHESES})


def cache_key(arm: str, model_id: str, rendered_prompt: str, question: str) -> str:
    payload = _json_dumps(
        {
            "arm": arm,
            "model_id": model_id,
            "rendered_prompt": rendered_prompt,
            "question": question,
        }
    )
    return _sha256(payload)


def _cache_path(run_dir: Path, key: str) -> Path:
    return run_dir / f"{key}.json"


def _read_cache(run_dir: Path, key: str) -> Any | None:
    path = _cache_path(run_dir, key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(run_dir: Path, key: str, value: Any) -> None:
    path = _cache_path(run_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def predict_llm(question: str, arm: str, model_id: str, cache_dir: Path, use_cache: bool = True) -> dict[str, Any]:
    system, user = render_llm_prompts(question)
    rendered_prompt = f"SYSTEM:\n{system}\nUSER:\n{user}"
    key = cache_key(arm, model_id, rendered_prompt, question)

    cached = _read_cache(cache_dir, key) if use_cache else None
    if cached is None:
        from app.retriever.llm_client import generate

        raw = generate(system, user, model=model_id)
        cached = {"raw": raw}
        if use_cache:
            _write_cache(cache_dir, key, cached)

    parsed = parse_prediction(cached["raw"])
    if parsed is None:
        return {
            "raw_prediction": cached["raw"],
            "predicted_intent": None,
            "confidence": None,
            "routed_prediction": "default",
            "parse_ok": False,
            "cache_key": key,
        }

    intent, confidence = parsed
    return {
        "raw_prediction": cached["raw"],
        "predicted_intent": intent,
        "confidence": confidence,
        "routed_prediction": intent if confidence == "high" else "default",
        "parse_ok": True,
        "cache_key": key,
    }


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    max_value = max(values)
    exps = [math.exp(value - max_value) for value in values]
    total = sum(exps)
    return [value / total for value in exps]


def _label_mapping(cross_encoder: Any) -> dict[str, int]:
    config = getattr(getattr(cross_encoder, "model", None), "config", None)
    label2id = getattr(config, "label2id", None)
    id2label = getattr(config, "id2label", None)

    mapping: dict[str, int] = {}
    if isinstance(label2id, dict):
        mapping.update({str(label).lower(): int(index) for label, index in label2id.items()})
    if isinstance(id2label, dict):
        mapping.update({str(label).lower(): int(index) for index, label in id2label.items()})
    return mapping


def entailment_index(cross_encoder: Any) -> int:
    mapping = _label_mapping(cross_encoder)
    for label, index in mapping.items():
        if "entail" in label:
            return index
    raise ValueError("NLI model config does not expose an entailment label")


def _as_matrix(raw_scores: Any) -> list[list[float]]:
    rows = raw_scores.tolist() if hasattr(raw_scores, "tolist") else raw_scores
    if not isinstance(rows, list) or not rows:
        raise ValueError("NLI model returned no scores")
    if not all(isinstance(row, list) for row in rows):
        raise ValueError("NLI model must return label scores for each hypothesis")
    return [[float(value) for value in row] for row in rows]


class NliScorer:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model: Any | None = None
        self._entailment_col: int | None = None

    def _load(self) -> tuple[Any, int]:
        if self._model is None or self._entailment_col is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_id)
            self._entailment_col = entailment_index(self._model)
        return self._model, self._entailment_col

    def score(self, question: str) -> dict[str, float]:
        model, entailment_col = self._load()
        pairs = [(question, NLI_HYPOTHESES[intent]) for intent in INTENTS]
        raw_scores = _as_matrix(model.predict(pairs))

        scores: dict[str, float] = {}
        for intent, row in zip(INTENTS, raw_scores, strict=True):
            if entailment_col >= len(row):
                raise ValueError("NLI entailment index is outside the returned score vector")
            scores[intent] = row[entailment_col]
        return scores


def predict_nli(
    question: str,
    model_id: str,
    cache_dir: Path,
    margin_threshold: float,
    scorer: NliScorer,
    use_cache: bool = True,
) -> dict[str, Any]:
    rendered_prompt = render_nli_prompt()
    key = cache_key("nli", model_id, rendered_prompt, question)
    cached = _read_cache(cache_dir, key) if use_cache else None
    if cached is None:
        scores = scorer.score(question)
        cached = {"scores": scores}
        if use_cache:
            _write_cache(cache_dir, key, cached)

    scores = {intent: float(cached["scores"][intent]) for intent in INTENTS}
    probabilities = dict(zip(INTENTS, _softmax([scores[intent] for intent in INTENTS]), strict=True))
    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    predicted_intent = ranked[0][0]
    margin = ranked[0][1] - ranked[1][1]
    confidence = "high" if margin >= margin_threshold else "low"
    return {
        "raw_prediction": {
            "entailment_scores": scores,
            "intent_probabilities": probabilities,
            "top2_softmax_margin": margin,
        },
        "predicted_intent": predicted_intent,
        "confidence": confidence,
        "routed_prediction": predicted_intent if confidence == "high" else "default",
        "parse_ok": True,
        "cache_key": key,
    }


def precision_recall_support(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, dict[str, float | int]]:
    metrics: dict[str, dict[str, float | int]] = {}
    for intent in INTENTS:
        tp = sum(1 for row in rows if row["gold"] == intent and row.get(prediction_key) == intent)
        fp = sum(1 for row in rows if row["gold"] != intent and row.get(prediction_key) == intent)
        fn = sum(1 for row in rows if row["gold"] == intent and row.get(prediction_key) != intent)
        support = sum(1 for row in rows if row["gold"] == intent)
        metrics[intent] = {
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "support": support,
        }
    return metrics


def accuracy(rows: list[dict[str, Any]], prediction_key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get(prediction_key) == row["gold"]) / len(rows)


def confusion_matrix(rows: list[dict[str, Any]], prediction_key: str) -> list[dict[str, Any]]:
    matrix = []
    for gold in INTENTS:
        row = {"gold": gold}
        for predicted in INTENTS:
            row[predicted] = sum(
                1
                for item in rows
                if item["gold"] == gold and item.get(prediction_key) == predicted
            )
        matrix.append(row)
    return matrix


def write_confusion_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["gold", *INTENTS])
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def score_arm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "raw": {
            "accuracy": accuracy(rows, "predicted_intent"),
            "per_intent": precision_recall_support(rows, "predicted_intent"),
        },
        "routed": {
            "accuracy": accuracy(rows, "routed_prediction"),
            "per_intent": precision_recall_support(rows, "routed_prediction"),
        },
        "parse_failures": sum(1 for row in rows if not row["parse_ok"]),
        "low_confidence": sum(1 for row in rows if row.get("confidence") == "low"),
    }


def majority_prediction(predictions: list[str]) -> str | None:
    counts = Counter(predictions)
    if not counts:
        return None
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def agreement_stats(arm_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    arms = list(arm_rows)
    by_question = {
        arm: {row["question"]: row for row in rows}
        for arm, rows in arm_rows.items()
    }
    questions = sorted(set.intersection(*(set(rows) for rows in by_question.values()))) if arms else []

    pairwise = {}
    for i, left in enumerate(arms):
        for right in arms[i + 1:]:
            agree = sum(
                1
                for question in questions
                if by_question[left][question]["routed_prediction"] == by_question[right][question]["routed_prediction"]
            )
            pairwise[f"{left}__{right}"] = {
                "agree": agree,
                "total": len(questions),
                "rate": agree / len(questions) if questions else 0.0,
            }

    key_table = {
        "all_agree": {"correct": 0, "wrong": 0},
        "any_disagree": {"correct": 0, "wrong": 0},
    }
    details = []
    for question in questions:
        routed = [by_question[arm][question]["routed_prediction"] for arm in arms]
        gold = by_question[arms[0]][question]["gold"]
        all_agree = len(set(routed)) == 1
        if all_agree:
            correct = routed[0] == gold
        else:
            majority = majority_prediction(routed)
            correct = majority == gold
        bucket = "all_agree" if all_agree else "any_disagree"
        outcome = "correct" if correct else "wrong"
        key_table[bucket][outcome] += 1
        details.append(
            {
                "question": question,
                "gold": gold,
                "agreement": bucket,
                "majority_or_unanimous_correct": correct,
                "routed_predictions": dict(zip(arms, routed, strict=True)),
            }
        )

    return {
        "arms": arms,
        "pairwise_routed_agreement": pairwise,
        "key_table": key_table,
        "rows": details,
    }


def create_run_dir() -> Path:
    started = datetime.now().astimezone()
    run_dir = Path(settings.eval_results_dir) / "runs" / started.strftime("%Y-%m-%d") / f"intent_ab_{started.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def default_cache_dir() -> Path:
    return Path(settings.eval_results_dir) / "intent_ab_cache"


def load_benchmark(dataset_path: Path, labels_path: Path) -> list[dict[str, str]]:
    dataset = _read_jsonl(dataset_path)
    labels = load_intent_labels(dataset_path, labels_path)
    return [{"question": row["question"], "gold": labels[row["question"]]} for row in dataset]


def assert_non_eval_smoke_pool(dataset_path: Path) -> None:
    eval_questions = {row["question"] for row in _read_jsonl(dataset_path)}
    overlap = sorted(question for question, _intent in SMOKE_QUESTIONS if question in eval_questions)
    if overlap:
        raise ValueError(f"smoke question overlaps benchmark: {overlap[:3]}")
    few_shot_overlap = sorted(question for question, _intent in FEW_SHOTS if question in eval_questions)
    if few_shot_overlap:
        raise ValueError(f"few-shot question overlaps benchmark: {few_shot_overlap[:3]}")


def run_arm(
    arm: str,
    items: list[dict[str, str]],
    run_dir: Path,
    cache_dir: Path,
    model_id: str,
    nli_margin_threshold: float,
    use_cache: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    nli_scorer = NliScorer(model_id) if arm == "nli" else None
    for index, item in enumerate(items, start=1):
        start = time.perf_counter()
        error = None
        try:
            if arm in LLM_ARMS:
                prediction = predict_llm(item["question"], arm, model_id, cache_dir, use_cache=use_cache)
            elif arm == "nli":
                prediction = predict_nli(
                    item["question"],
                    model_id,
                    cache_dir,
                    nli_margin_threshold,
                    scorer=nli_scorer,
                    use_cache=use_cache,
                )
            else:
                raise ValueError(f"unknown arm {arm!r}")
        except Exception as exc:
            prediction = {
                "raw_prediction": "",
                "predicted_intent": None,
                "confidence": None,
                "routed_prediction": "default",
                "parse_ok": False,
                "cache_key": None,
            }
            error = str(exc)

        row = {
            "row_index": index,
            "arm": arm,
            "question": item["question"],
            "gold": item["gold"],
            **prediction,
            "error": error,
            "elapsed_s": round(time.perf_counter() - start, 3),
        }
        rows.append(row)
        status = "OK" if error is None else "ERR"
        print(f"[{arm}] [{index}/{len(items)}] {status} {row['routed_prediction']} gold={item['gold']}", flush=True)
    return rows


def parse_arms(value: str) -> list[str]:
    arms = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(arms) - set(ALL_ARMS))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown arm(s): {', '.join(unknown)}")
    return arms


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run R1 intent-classifier A/B arms.")
    parser.add_argument("--arms", type=parse_arms, default=ALL_ARMS, help="Comma-separated arms: mistral,haiku,nli")
    parser.add_argument("--dataset", type=Path, default=Path(settings.eval_dataset_path))
    parser.add_argument("--labels", type=Path, default=Path(settings.eval_intent_labels_path))
    parser.add_argument("--mistral-model", default=DEFAULT_MODELS["mistral"])
    parser.add_argument("--haiku-model", default=DEFAULT_MODELS["haiku"])
    parser.add_argument("--nli-model", default=DEFAULT_MODELS["nli"])
    parser.add_argument("--qwen3-model", default=DEFAULT_MODELS["qwen3"])
    parser.add_argument("--gemma3-model", default=DEFAULT_MODELS["gemma3"])
    parser.add_argument("--nli-margin-threshold", type=float, default=DEFAULT_NLI_MARGIN_THRESHOLD)
    parser.add_argument("--smoke-only", action="store_true", help="Run only the non-eval smoke pool.")
    parser.add_argument("--cache-dir", type=Path, default=default_cache_dir(), help="Shared crash/resume cache directory.")
    parser.add_argument("--no-cache", action="store_true", help="Disable crash/resume cache writes and reads.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    dataset_path = args.dataset
    labels_path = args.labels
    cache_dir = args.cache_dir
    assert_non_eval_smoke_pool(dataset_path)

    run_dir = create_run_dir()
    model_ids = {
        "mistral": args.mistral_model,
        "haiku": args.haiku_model,
        "nli": args.nli_model,
        "qwen3": args.qwen3_model,
        "gemma3": args.gemma3_model,
    }
    system_template, user_template = render_llm_prompts("{question}")
    prompt_text = f"SYSTEM:\n{system_template}\nUSER:\n{user_template}"
    meta = {
        "kind": "intent_classifier_ab",
        "created_at": datetime.now().astimezone().isoformat(),
        "git_sha": _git_sha(),
        "arms": args.arms,
        "model_ids": {arm: model_ids[arm] for arm in args.arms},
        "dataset_path": str(dataset_path),
        "labels_path": str(labels_path),
        "cache_dir": str(cache_dir),
        "prompt_hash": _sha256(prompt_text),
        "nli_hypotheses_hash": _sha256(render_nli_prompt()),
        "few_shot_source": "hand-authored non-eval examples in app/retriever/intent_router.py",
        "smoke_question_source": "hand-authored non-eval smoke pool in scripts/classify_intent_ab.py",
        "prompt_iteration_policy": "Prompt and hypotheses are authored once, smoke-tested off-benchmark, then benchmarked once. Cache is for crash/resume only.",
        "nli_margin_threshold": args.nli_margin_threshold,
        "nli_threshold_status": "pre-registered before benchmark scoring; no threshold sweep over benchmark rows",
        "writes_latest_or_manifest": False,
    }
    _write_json(run_dir / "meta.json", meta)

    if args.smoke_only:
        items = [{"question": question, "gold": gold} for question, gold in SMOKE_QUESTIONS]
    else:
        items = load_benchmark(dataset_path, labels_path)

    arm_rows = {}
    for arm in args.arms:
        rows = run_arm(
            arm,
            items,
            run_dir,
            cache_dir,
            model_ids[arm],
            args.nli_margin_threshold,
            use_cache=not args.no_cache,
        )
        arm_rows[arm] = rows
        write_jsonl(run_dir / f"predictions_{arm}.jsonl", rows)
        _write_json(run_dir / f"metrics_{arm}.json", score_arm(rows))
        write_confusion_csv(run_dir / f"confusion_{arm}.csv", confusion_matrix(rows, "routed_prediction"))

    if len(arm_rows) > 1:
        _write_json(run_dir / "agreement.json", agreement_stats(arm_rows))

    print(f"\nWrote intent A/B artifacts to {run_dir}")


if __name__ == "__main__":
    main()
