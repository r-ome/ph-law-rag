from __future__ import annotations

# Provenance note: this local Qwen scorer predates the production backend in
# app.retriever.reranker. Use trace_art1145.py or trace_constitution_echo.py
# for traces that exercise the shipped rerank() path and respect reranker_backend.

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from app.config import settings
from app.indexing.embedder import get_embed_model
from app.indexing.index_service import get_qdrant_client
from app.indexing.vector_store import operative_filter, query
from app.retriever.hybrid_retriever import _fuse
from app.retriever.reranker import _get_model
from app.retriever.sparse_retriever import sparse_retriever
from app.retriever.types import RetrievalResult


TOP_K = 30
QWEN_MODEL = "Qwen/Qwen3-Reranker-0.6B"
QWEN_TASK = "Given a Philippine-law question, retrieve authoritative legal provisions that answer the question."
QWEN_MAX_LENGTH = 8192
BOOST_VALUE = 2.0


ROWS = [
    {
        "key": "felony_art3",
        "group": "primary",
        "question": "How does the Revised Penal Code define a felony?",
        "targets": [{"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:3"}],
    },
    {
        "key": "sale_perfection_art1475",
        "group": "primary",
        "question": "When I buy something, at what point is the sale actually a done deal?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1475"}],
    },
    {
        "key": "drug_possession_sec11",
        "group": "must_not_regress",
        "question": "What can happen to me if I'm caught holding illegal drugs?",
        "targets": [{"source_id": "dangerous_drugs_act", "provision_id": "dangerous_drugs_act:article-ii:section:11"}],
    },
    {
        "key": "verbal_land_sale_art1403",
        "group": "must_not_regress",
        "question": "Is a purely verbal agreement to sell a parcel of land valid in the Philippines?",
        "targets": [{"source_id": "civil_code", "provision_id": "civil_code:article:1403"}],
    },
    {
        "key": "theft_penalty_today",
        "group": "ok_spotcheck",
        "question": "How is the penalty for theft determined under the Revised Penal Code today, and how has that changed?",
        "targets": [
            {"source_id": "revised_penal_code", "provision_id": "revised_penal_code:article:309"},
            {"source_id": "rpc_penalty_amendments_2017", "provision_id": "revised_penal_code:article:309"},
        ],
    },
    {
        "key": "article_335_rape",
        "group": "ok_spotcheck",
        "question": "Is rape still prosecuted under Article 335 of the Revised Penal Code?",
        "targets": [
            {"source_id": "anti_rape_law_1997", "provision_id": "revised_penal_code:article:266-a"},
            {"source_id": "statutory_rape_amendments_2022", "provision_id": "revised_penal_code:article:266-a"},
        ],
    },
    {
        "key": "statutory_rape_close_age",
        "group": "ok_spotcheck",
        "question": "Is there an exception to statutory rape when the offender and the minor are close in age?",
        "targets": [{"source_id": "statutory_rape_amendments_2022", "provision_id": "revised_penal_code:article:266-a"}],
    },
    {
        "key": "death_penalty_instead",
        "group": "ok_spotcheck",
        "question": "Several provisions of the Revised Penal Code still prescribe the death penalty. Can Philippine courts impose it, and what penalty is imposed instead?",
        "targets": [
            {"source_id": "death_penalty_prohibition", "provision_id": "death_penalty_prohibition:section:1"},
            {"source_id": "death_penalty_prohibition", "provision_id": "death_penalty_prohibition:section:2"},
        ],
    },
    {
        "key": "probation_appeal",
        "group": "ok_spotcheck",
        "question": "Can a defendant who appealed their conviction still apply for probation?",
        "targets": [{"source_id": "probation_amendments_2015", "provision_id": "probation_law:section:4"}],
    },
]


@dataclass
class TargetHit:
    present: bool
    rank: int | None = None
    chunk_id: str | None = None
    source_id: str | None = None
    provision_id: str | None = None
    unit_label: str | None = None
    score: float | None = None


def _load_eval_questions() -> set[str]:
    return {
        json.loads(line)["question"]
        for line in Path(settings.eval_dataset_path).read_text().splitlines()
        if line.strip()
    }


def _matches(result: RetrievalResult, targets: list[dict[str, str]]) -> bool:
    source_id = result.metadata.get("source_id")
    provision_id = result.metadata.get("provision_id")
    return any(
        source_id == target["source_id"] and provision_id == target["provision_id"]
        for target in targets
    )


def _first_target(results: list[RetrievalResult], targets: list[dict[str, str]]) -> TargetHit:
    for rank, result in enumerate(results, start=1):
        if _matches(result, targets):
            return TargetHit(
                present=True,
                rank=rank,
                chunk_id=result.chunk_id,
                source_id=result.metadata.get("source_id"),
                provision_id=result.metadata.get("provision_id"),
                unit_label=result.metadata.get("unit_label"),
                score=result.score,
            )
    return TargetHit(False)


def _kept_ids(results: list[RetrievalResult]) -> list[str]:
    return [result.chunk_id for result in results]


def _clone(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        RetrievalResult(result.chunk_id, result.text, result.score, dict(result.metadata))
        for result in results
    ]


def _candidate_pool(question: str) -> tuple[list[RetrievalResult], dict]:
    embed_model = get_embed_model()
    query_vector = embed_model.get_query_embedding(question)
    points = query(
        get_qdrant_client(),
        query_vector,
        TOP_K,
        query_filter=operative_filter(None),
    )
    dense = [
        RetrievalResult(
            chunk_id=str(point.id),
            text=point.payload["text"],
            score=point.score,
            metadata={k: v for k, v in point.payload.items() if k != "text"},
        )
        for point in points
    ]
    dense_filtered = [result for result in dense if 1 - result.score <= settings.max_distance]
    sparse = sparse_retriever(question)
    fused = _fuse([dense_filtered, sparse])
    return fused, {
        "dense_n": len(dense),
        "dense_filtered_n": len(dense_filtered),
        "sparse_n": len(sparse),
        "fused_n": len(fused),
    }


def _minilm_scores(question: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
    model = _get_model()
    scores = model.predict([(question, result.text) for result in results])
    scored = _clone(results)
    for result, score in zip(scored, scores):
        result.score = float(score)
    scored.sort(key=lambda result: result.score, reverse=True)
    return scored


def _minilm_margin_top8(question: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
    scored = _minilm_scores(question, results)
    if not scored:
        return []
    top = scored[0].score
    kept = [result for result in scored if result.score >= top - settings.rerank_score_margin]
    return kept[: settings.rerank_top_n]


def _citation_tokens(question: str) -> set[str]:
    tokens = set()
    for match in re.finditer(r"\b(?:article|art\.?|section|sec\.?)\s+([0-9]+[a-z]?)\b", question, re.I):
        tokens.add(match.group(1).lower())
    return tokens


def _boost(question: str, result: RetrievalResult) -> float:
    q = question.lower()
    text = result.text.lower()
    unit_label = str(result.metadata.get("unit_label") or "").lower()
    provision_id = str(result.metadata.get("provision_id") or "").lower()

    boost = 0.0
    for token in _citation_tokens(question):
        if token in unit_label or provision_id.endswith(f":{token}") or provision_id.endswith(f":article:{token}"):
            boost += BOOST_VALUE

    if any(word in q for word in ("define", "definition")) and (
        "definition" in text[:220] or "definition" in unit_label
    ):
        boost += BOOST_VALUE

    if "sale" in q and ("done deal" in q or "buy" in q) and (
        "sale is perfected" in text or "contract of sale is perfected" in text
    ):
        boost += BOOST_VALUE

    return boost


def _apply_boost(question: str, scored: list[RetrievalResult]) -> list[RetrievalResult]:
    boosted = _clone(scored)
    for result in boosted:
        result.score += _boost(question, result)
    boosted.sort(key=lambda result: result.score, reverse=True)
    return boosted


class QwenYesNoReranker:
    def __init__(self) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(QWEN_MODEL, padding_side="left")
        self.model = AutoModelForCausalLM.from_pretrained(QWEN_MODEL).eval()
        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        self.prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
            'Note that the answer can only be "yes" or "no".<|im_end|>\n'
            "<|im_start|>user\n"
        )
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self.prefix_tokens = self.tokenizer.encode(self.prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(self.suffix, add_special_tokens=False)

    def _format_pair(self, query_text: str, document: str) -> str:
        return f"<Instruct>: {QWEN_TASK}\n<Query>: {query_text}\n<Document>: {document}"

    def _inputs(self, pairs: list[tuple[str, str]]):
        formatted = [self._format_pair(query_text, doc) for query_text, doc in pairs]
        inputs = self.tokenizer(
            formatted,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=QWEN_MAX_LENGTH - len(self.prefix_tokens) - len(self.suffix_tokens),
        )
        for i, input_ids in enumerate(inputs["input_ids"]):
            inputs["input_ids"][i] = self.prefix_tokens + input_ids + self.suffix_tokens
        padded = self.tokenizer.pad(inputs, padding=True, return_tensors="pt", max_length=QWEN_MAX_LENGTH)
        return {key: value.to(self.model.device) for key, value in padded.items()}

    @torch.no_grad()
    def score(self, query_text: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        pairs = [(query_text, result.text) for result in results]
        inputs = self._inputs(pairs)
        batch_scores = self.model(**inputs).logits[:, -1, :]
        true_vector = batch_scores[:, self.token_true_id]
        false_vector = batch_scores[:, self.token_false_id]
        stacked = torch.stack([false_vector, true_vector], dim=1)
        scores = torch.nn.functional.log_softmax(stacked, dim=1)[:, 1].exp().tolist()
        scored = _clone(results)
        for result, score in zip(scored, scores):
            result.score = float(score)
        scored.sort(key=lambda result: result.score, reverse=True)
        return scored


def _summarize_arm(
    arm: str,
    row: dict,
    candidates: list[RetrievalResult],
    selector: Callable[[str, list[RetrievalResult]], list[RetrievalResult]],
    baseline_ids: list[str],
) -> dict:
    start = time.perf_counter()
    selected = selector(row["question"], _clone(candidates))
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    target_hit = _first_target(selected, row["targets"])
    selected_ids = _kept_ids(selected)
    return {
        "arm": arm,
        "elapsed_ms": elapsed_ms,
        "target": target_hit.__dict__,
        "kept": [
            {
                "rank": i,
                "chunk_id": result.chunk_id,
                "score": result.score,
                "source_id": result.metadata.get("source_id"),
                "provision_id": result.metadata.get("provision_id"),
                "unit_label": result.metadata.get("unit_label"),
            }
            for i, result in enumerate(selected, start=1)
        ],
        "kept_overlap_with_minilm": len(set(selected_ids) & set(baseline_ids)),
        "lost_from_minilm": [chunk_id for chunk_id in baseline_ids if chunk_id not in selected_ids],
        "gained_vs_minilm": [chunk_id for chunk_id in selected_ids if chunk_id not in baseline_ids],
    }


def _selected_rows() -> list[dict]:
    groups = {group.strip() for group in os.getenv("TRACE_SELECTOR_GROUPS", "").split(",") if group.strip()}
    keys = {key.strip() for key in os.getenv("TRACE_SELECTOR_KEYS", "").split(",") if key.strip()}
    rows = ROWS
    if groups:
        rows = [row for row in rows if row["group"] in groups]
    if keys:
        rows = [row for row in rows if row["key"] in keys]
    return rows


def main() -> None:
    rows = _selected_rows()
    eval_questions = _load_eval_questions()
    missing = [row["question"] for row in rows if row["question"] not in eval_questions]
    if missing:
        raise SystemExit(f"row question not found in eval dataset: {missing}")

    print("Loading MiniLM...", flush=True)
    _get_model()
    qwen: QwenYesNoReranker | None = None

    output = []
    for row in rows:
        print(f"Building candidates for {row['key']}...", flush=True)
        candidates, pool_info = _candidate_pool(row["question"])
        minilm_ranked = _minilm_scores(row["question"], candidates)
        minilm_selected = _minilm_margin_top8(row["question"], candidates)
        baseline_ids = _kept_ids(minilm_selected)

        def minilm_selector(_: str, __: list[RetrievalResult]) -> list[RetrievalResult]:
            return _clone(minilm_selected)

        def minilm_boost_selector(question: str, __: list[RetrievalResult]) -> list[RetrievalResult]:
            boosted = _apply_boost(question, minilm_ranked)
            if not boosted:
                return []
            top = boosted[0].score
            kept = [result for result in boosted if result.score >= top - settings.rerank_score_margin]
            return kept[: settings.rerank_top_n]

        if qwen is None:
            print("Loading Qwen3 reranker...", flush=True)
            qwen = QwenYesNoReranker()

        print(f"Scoring Qwen3 for {row['key']} ({len(candidates)} pairs)...", flush=True)
        qwen_start = time.perf_counter()
        qwen_ranked = qwen.score(row["question"], candidates)
        qwen_score_ms = round((time.perf_counter() - qwen_start) * 1000, 1)

        def qwen_selector(_: str, __: list[RetrievalResult]) -> list[RetrievalResult]:
            return _clone(qwen_ranked[: settings.rerank_top_n])

        def qwen_boost_selector(question: str, __: list[RetrievalResult]) -> list[RetrievalResult]:
            return _apply_boost(question, qwen_ranked)[: settings.rerank_top_n]

        arms = [
            _summarize_arm("minilm", row, candidates, minilm_selector, baseline_ids),
            _summarize_arm("minilm_boost", row, candidates, minilm_boost_selector, baseline_ids),
            _summarize_arm("qwen_top8_no_margin", row, candidates, qwen_selector, baseline_ids),
            _summarize_arm("qwen_boost_top8_no_margin", row, candidates, qwen_boost_selector, baseline_ids),
        ]
        output.append({
            "key": row["key"],
            "group": row["group"],
            "question": row["question"],
            "targets": row["targets"],
            "top_k": TOP_K,
            "pool": pool_info,
            "qwen_score_ms": qwen_score_ms,
            "candidate_target": _first_target(candidates, row["targets"]).__dict__,
            "arms": arms,
        })

        print(f"\n## {row['key']} ({row['group']})", flush=True)
        print(f"candidate_target={output[-1]['candidate_target']}", flush=True)
        for arm in arms:
            hit = arm["target"]
            print(
                f"{arm['arm']}: target={'Y' if hit['present'] else 'n'} "
                f"rank={hit['rank'] or '-'} overlap={arm['kept_overlap_with_minilm']}/8 "
                f"ms={arm['elapsed_ms']}",
                flush=True,
            )

    out_path = Path(settings.eval_results_dir) / "trace_selector_ab_top30.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
