from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.retriever.context_selection import select_context
from app.retriever.types import RetrievalResult


PROBES = [
    {
        "key": "due_process_equal_protection",
        "question": "Where does the Constitution protect due process and equal protection?",
        "target": "constitution_1987:article-iii:section:1",
    },
    {
        "key": "searches_seizures",
        "question": "What constitutional rule protects people against unreasonable searches and seizures?",
        "target": "constitution_1987:article-iii:section:2",
    },
    {
        "key": "privacy_communications",
        "question": "What does the Constitution say about privacy of communication and correspondence?",
        "target": "constitution_1987:article-iii:section:3",
    },
    {
        "key": "speech_press_assembly",
        "question": "What constitutional provision protects speech, press, assembly, and petition?",
        "target": "constitution_1987:article-iii:section:4",
    },
    {
        "key": "religion",
        "question": "What constitutional provision protects free exercise of religion?",
        "target": "constitution_1987:article-iii:section:5",
    },
]
ECHO = "civil_code:article:32"


def _rank(results: list[RetrievalResult], provision_id: str) -> dict:
    for i, result in enumerate(results, start=1):
        if result.metadata.get("provision_id") == provision_id:
            return {
                "present": True,
                "rank": i,
                "chunk_id": result.chunk_id,
                "score": result.score,
                "source_id": result.metadata.get("source_id"),
                "unit_label": result.metadata.get("unit_label"),
            }
    return {"present": False}


def main() -> None:
    rows = []
    inversions = 0
    for probe in PROBES:
        selection = select_context(probe["question"])
        target = _rank(selection.pre_expansion, probe["target"])
        echo = _rank(selection.pre_expansion, ECHO)
        inverted = bool(
            target.get("present")
            and echo.get("present")
            and target.get("rank", 0) > echo.get("rank", 0)
        )
        inversions += int(inverted)
        rows.append({
            **probe,
            "target_hit": target,
            "echo_hit": echo,
            "constitution_below_echo": inverted,
        })
        print(
            f"{probe['key']}: target_rank={target.get('rank', '-')} "
            f"echo_rank={echo.get('rank', '-')} inverted={inverted}",
            flush=True,
        )

    output = {
        "reranker_backend": settings.reranker_backend,
        "dense_top_k": settings.dense_top_k,
        "inversions": inversions,
        "authority_rank_recommended": inversions >= 3,
        "rows": rows,
    }
    out_path = Path(settings.eval_results_dir) / "trace_constitution_echo.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nInversions: {inversions}/{len(PROBES)}")
    print(f"authority_rank_recommended={output['authority_rank_recommended']}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
