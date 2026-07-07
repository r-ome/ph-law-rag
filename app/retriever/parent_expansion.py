"""Post-rerank parent expansion (Option B).

Runs AFTER rerank + cutoff (and edge expansion), BEFORE build_context. When >= N
leaves of the same parent section survive the cutoff, swap them for the whole parent
section so generation sees the full enumeration/list. Retrieval precision still comes
from the fine leaves; this only changes what generation reads.

Ordering: iterate survivors in rank order and emit the parent once, at the slot of its
best-ranked child — context order stays faithful to rerank priority. No partial
truncation: if a parent would exceed max_chars, fall back to the leaves exactly as
retrieved (truncating legal provisions is the bug this whole track exists to fix).
"""

from collections import Counter

from app.config import settings
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult


def _load_parents(keys: set[str]) -> dict[str, dict]:
    from app.db import get_connection

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT * FROM chunk_parents WHERE parent_key IN ({placeholders})",
            list(keys),
        ).fetchall()
        return {row["parent_key"]: dict(row) for row in rows}
    finally:
        conn.close()


def _parent_result(row: dict, child_count: int, score: float, child_meta: dict) -> RetrievalResult:
    metadata = {
        "expanded_from_parent": True,
        "parent_key": row["parent_key"],
        "child_count": child_count,
        "doc_id": row["doc_id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "url": row["url"],
        "is_structural": True,
        "unit_type": row["unit_type"],
        "unit_label": row["unit_label"],
        "structure_path": row["structure_path"],
    }
    # chunk_parents has no operability columns; carry them from the triggering child so debug
    # traces/citations keep the provision identity (a parent = one provision at v1 granularity).
    # Suppression already happened upstream in retrieval, so this never re-surfaces a hidden chunk.
    for key in (
        "provision_id",
        "provision_status",
        "operability_action",
        "operability_basis_source_id",
        "consolidated",
        "consolidation_basis",
    ):
        if key in child_meta:
            metadata[key] = child_meta[key]
    return RetrievalResult(
        chunk_id=row["parent_key"],
        text=row["text"],
        score=score,                       # carry the best child's score; nothing reorders downstream
        metadata=metadata,
    )


def expand_parents(
    results: list[RetrievalResult],
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    enabled = knobs.parent_expansion_enabled if knobs else settings.parent_expansion_enabled
    if not enabled:
        return results

    # parent_has_hidden_leaves means the parent text contains leaves hidden by a
    # provision_status override. Swapping that parent in would resurrect superseded text;
    # deliberate v1 trade: keep fragments rather than leak stale text.
    counts = Counter(
        r.metadata.get("parent_key")
        for r in results
        if r.metadata.get("parent_key") and not r.metadata.get("parent_has_hidden_leaves")
    )
    eligible = {
        pk for pk, n in counts.items()
        if pk and n >= settings.parent_expansion_min_children
    }
    if not eligible:
        return results

    parents = _load_parents(eligible)

    out: list[RetrievalResult] = []
    budget = 0
    covered: set[str] = set()   # parent already emitted
    skipped: set[str] = set()   # parent decided unmergeable (missing row / over budget) — decide once
    for r in results:
        pk = r.metadata.get("parent_key")
        if not pk or pk not in eligible:
            out.append(r)
            continue
        if pk in covered:
            continue            # later leaf of an already-emitted parent → absorbed
        if pk in skipped:
            out.append(r)
            continue
        row = parents.get(pk)
        if row and budget + row["char_count"] <= settings.parent_expansion_max_chars:
            out.append(_parent_result(row, counts[pk], r.score, r.metadata))
            budget += row["char_count"]
            covered.add(pk)
            continue
        skipped.add(pk)         # fall back to leaves exactly as retrieved (no truncation)
        out.append(r)
    return out
