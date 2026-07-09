#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.config import settings
from app.conversation.session import append_turn, create_session
from app.db import get_connection, init_db
from app.retriever.answer_service import run_answer


DEFAULT_FIXTURE = Path("tests/fixtures/pipeline_golden.json")

CASES = [
    {
        "label": "greeting",
        "question": "hi",
        "debug": True,
    },
    {
        "label": "normal_answer",
        "question": "What is theft under Article 308 of the Revised Penal Code?",
        "debug": True,
    },
    {
        "label": "current_law_router",
        "question": "Which law controls if a later statute amended an older penalty provision?",
        "debug": True,
    },
    {
        "label": "soft_abstain_out_of_scope",
        "question": "What does Philippine law say about the mineral rights of a fictional asteroid colony?",
        "debug": True,
    },
    {
        "label": "soft_abstain",
        "question": "Answer only if the retrieved context states the exact filing deadline for this unknown local ordinance.",
        "debug": True,
    },
    {
        "label": "session_rewrite",
        "question": "What is its penalty?",
        "debug": True,
        "session_id": "golden-session-rewrite",
        "seed_turns": [
            {
                "question": "What is theft under Philippine law?",
                "rewritten_question": "What is theft under Philippine law?",
                "answer": "The previous topic was theft under Philippine law.",
                "retrieved_chunks_json": "[]",
                "sources_json": "[]",
            }
        ],
    },
]


def _reset_session(session_id: str, seed_turns: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM conversation_turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()

    create_session(session_id=session_id)
    for turn in seed_turns:
        append_turn(session_id, turn)


def _strip_stage_ms(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_stage_ms(item)
            for key, item in value.items()
            if not (key == "ms" and "name" in value)
        }
    if isinstance(value, list):
        return [_strip_stage_ms(item) for item in value]
    return value


def _normalize_pair(pair: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(pair)
    response = normalized.get("response")
    if response:
        # Ollama/Mistral can vary generated wording even with temperature=0 and
        # seed=42 on local hardware. PR1's no-op proof is the byte-identical
        # generation input: contexts, selected chunks, prompt length, routing,
        # strategy, and trace shape. Sources are derived by re-parsing [n]
        # citations from the generated answer, so normalize them together.
        response.pop("answer", None)
        response.pop("sources", None)
    trace = normalized.get("trace_record")
    if trace:
        for key in ("trace_id", "timestamp", "latency_ms"):
            trace.pop(key, None)
    return _strip_stage_ms(normalized)


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    session_id = case.get("session_id")
    if session_id:
        _reset_session(session_id, case.get("seed_turns", []))

    response, trace_record = run_answer(
        case["question"],
        debug=case.get("debug", True),
        session_id=session_id,
        trace=True,
        trace_label=f"golden:{case['label']}",
        strategy_override=case.get("strategy_override"),
    )
    return _normalize_pair(
        {
            "label": case["label"],
            "question": case["question"],
            "response": response,
            "trace_record": trace_record,
        }
    )


def _run_all() -> list[dict[str, Any]]:
    init_db()
    print(
        "Running golden pipeline check with "
        f"db={settings.db_path}, qdrant={settings.qdrant_url}, model={settings.llm_model}",
        flush=True,
    )
    results = []
    for case in CASES:
        print(f"- {case['label']}", flush=True)
        results.append(_run_case(case))
    return results


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def capture(path: Path) -> None:
    results = _run_all()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(results) + "\n", encoding="utf-8")
    print(f"Captured {len(results)} cases to {path}")


def compare(path: Path) -> None:
    expected = [_normalize_pair(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    actual = _run_all()
    if actual == expected:
        print(f"Golden check passed: {len(actual)} cases match {path}")
        return

    diff = difflib.unified_diff(
        _dump(expected).splitlines(),
        _dump(actual).splitlines(),
        fromfile=str(path),
        tofile="current",
        lineterm="",
    )
    print("\n".join(diff))
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture or compare live answer-pipeline golden outputs."
    )
    parser.add_argument("mode", choices=("capture", "compare"))
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    if args.mode == "capture":
        capture(args.fixture)
    else:
        compare(args.fixture)


if __name__ == "__main__":
    main()
