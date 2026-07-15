"""Generation-only implementation for frozen retrieval records.

Keep imports here intentionally narrow. In particular, this module must remain
usable after a retrieval process has released its reranker and embedding models.
"""

import hashlib
import json
import re
from typing import Any

from app.retriever.context_builder import build_context
from app.retriever.llm_client import LLMError, generate
from app.retriever.prompts import (
    LATER_ENACTED_RULE,
    SELFCHECK_SYSTEM,
    SYSTEM_PROMPT,
    build_selfcheck_prompt,
    build_user_prompt,
    is_abstention,
)
from app.retriever.types import RetrievalResult

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _cited_sources(answer_text: str, sources: list[dict]) -> list[dict]:
    refs = {int(m.group(1)) for m in _CITATION_RE.finditer(answer_text)}
    return [source for source in sources if source.get("ref") in refs] if refs else []


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def rehydrate_results(selected: list[dict[str, Any]]) -> list[RetrievalResult]:
    return [
        RetrievalResult(
            chunk_id=str(item["chunk_id"]),
            text=str(item.get("text", "")),
            score=float(item.get("score", 0.0)),
            metadata=dict(item.get("metadata", {}) or {}),
        )
        for item in selected
    ]


def prepare_prompts(
    question: str,
    selected: list[RetrievalResult],
    *,
    later_enacted_preference: bool = False,
) -> tuple[str, list[dict], str, str]:
    context_block, sources = build_context(selected)
    system_prompt = SYSTEM_PROMPT + (LATER_ENACTED_RULE if later_enacted_preference else "")
    user_prompt = build_user_prompt(question, context_block)
    return context_block, sources, system_prompt, user_prompt


def generate_frozen(
    *,
    question: str,
    selected: list[dict[str, Any]],
    model: str,
    later_enacted_preference: bool = False,
    selfcheck_enabled: bool = False,
    generate_fn=generate,
    build_context_fn=build_context,
    prepared: tuple[str, list[dict], str, str] | None = None,
) -> dict[str, Any]:
    results = rehydrate_results(selected)
    if prepared is None:
        context_block, sources = build_context_fn(results)
        system_prompt = SYSTEM_PROMPT + (
            LATER_ENACTED_RULE if later_enacted_preference else ""
        )
        user_prompt = build_user_prompt(question, context_block)
    else:
        context_block, sources, system_prompt, user_prompt = prepared
    try:
        answer_text = generate_fn(system_prompt, user_prompt, model=model)
    except LLMError as exc:
        return {
            "answer": f"The language model could not be reached: {exc}",
            "sources": [],
            "contexts": [r.text for r in results],
            "context_sources": [r.metadata.get("source_id", "") for r in results],
            "abstained": False,
            "error": True,
            "prompt": user_prompt,
            "context_block": context_block,
            "source_map": sources,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

    if selfcheck_enabled and not is_abstention(answer_text):
        try:
            revised = generate_fn(
                SELFCHECK_SYSTEM,
                build_selfcheck_prompt(question, context_block, answer_text),
                model=model,
            )
            if revised.strip():
                answer_text = revised
        except LLMError:
            pass

    abstained = is_abstention(answer_text)
    return {
        "answer": answer_text,
        "sources": [] if abstained else _cited_sources(answer_text, sources),
        "contexts": [r.text for r in results],
        "context_sources": [r.metadata.get("source_id", "") for r in results],
        "abstained": abstained,
        "error": False,
        "prompt": user_prompt,
        "context_block": context_block,
        "source_map": sources,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }


def replay_frozen(record: dict[str, Any], *, model_override: str | None = None) -> dict[str, Any]:
    """Validate prompt/context identities, then generate without live retrieval."""
    selected = record.get("selected_results")
    if not isinstance(selected, list) or record.get("selected_context_hash") != _json_hash(selected):
        raise ValueError("selected-context hash mismatch")
    terminal = record.get("terminal_response")
    if terminal is not None:
        if not isinstance(terminal, dict):
            raise ValueError("terminal_response must be an object")
        return {
            **terminal,
            "prompt": None,
            "context_block": "",
            "source_map": [],
            "system_prompt": "",
            "user_prompt": "",
            "generation_skipped": True,
        }
    results = rehydrate_results(selected)
    context, sources, system, user = prepare_prompts(
        record["effective_question"], results,
        later_enacted_preference=bool(record.get("policy", {}).get("later_enacted_preference_enabled")),
    )
    for key, value in {
        "context_block_hash": _text_hash(context),
        "source_map_hash": _json_hash(sources),
        "system_prompt_hash": _text_hash(system),
        "user_prompt_hash": _text_hash(user),
    }.items():
        if record.get(key) != value:
            raise ValueError(f"{key} mismatch")
    if record.get("source_map") != sources:
        raise ValueError("source_map content mismatch")
    if record.get("system_prompt") != system or record.get("user_prompt") != user:
        raise ValueError("rendered prompt content mismatch")
    model = model_override or record["model_choice"]["model"]
    return generate_frozen(
        question=record["effective_question"],
        selected=record["selected_results"],
        model=model,
        later_enacted_preference=bool(record.get("policy", {}).get("later_enacted_preference_enabled")),
        selfcheck_enabled=bool(record.get("policy", {}).get("selfcheck_enabled")),
        prepared=(context, sources, system, user),
    )
