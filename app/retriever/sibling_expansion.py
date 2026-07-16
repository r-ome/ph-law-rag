"""Bounded post-rerank expansion to adjacent structured legal leaves."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import settings
from app.retriever.strategy import RetrievalKnobs
from app.retriever.types import RetrievalResult


@dataclass(frozen=True)
class _SiblingChunk:
    chunk_id: str
    chunk_index: int
    text: str
    char_count: int
    token_estimate: int
    metadata: dict


@dataclass(frozen=True)
class _SiblingLeaf:
    identity: tuple[str, str]
    chunks: tuple[_SiblingChunk, ...]

    @property
    def char_count(self) -> int:
        return sum(chunk.char_count for chunk in self.chunks)

    @property
    def token_estimate(self) -> int:
        return sum(chunk.token_estimate for chunk in self.chunks)


def _load_families(parent_keys: set[str]) -> dict[str, list[_SiblingLeaf]]:
    """Load sibling families from SQLite; result metadata handles eligibility."""
    if not parent_keys:
        return {}

    from app.db import get_connection

    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in parent_keys)
        rows = conn.execute(
            f"""
            SELECT chunk_id, chunk_index, text, char_count, token_estimate, metadata_json
            FROM chunks
            WHERE json_extract(metadata_json, '$.parent_key') IN ({placeholders})
              AND json_extract(metadata_json, '$.unit_label') IS NOT NULL
            ORDER BY json_extract(metadata_json, '$.parent_key'), chunk_index, chunk_id
            """,
            sorted(parent_keys),
        ).fetchall()
    finally:
        conn.close()

    chunks_by_parent: dict[str, list[_SiblingChunk]] = {}
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        parent_key = metadata.get("parent_key")
        unit_label = metadata.get("unit_label")
        if not parent_key or not unit_label:
            continue
        text = str(row["text"] or "")
        chunks_by_parent.setdefault(str(parent_key), []).append(
            _SiblingChunk(
                chunk_id=str(row["chunk_id"]),
                chunk_index=int(row["chunk_index"] or 0),
                text=text,
                char_count=int(row["char_count"] or len(text)),
                token_estimate=int(row["token_estimate"] or 0),
                metadata=metadata,
            )
        )

    families: dict[str, list[_SiblingLeaf]] = {}
    for parent_key, chunks in chunks_by_parent.items():
        grouped: dict[tuple[str, str], list[_SiblingChunk]] = {}
        first_index: dict[tuple[str, str], int] = {}
        for chunk in chunks:
            identity = (parent_key, str(chunk.metadata["unit_label"]))
            grouped.setdefault(identity, []).append(chunk)
            first_index.setdefault(identity, chunk.chunk_index)
        families[parent_key] = [
            _SiblingLeaf(identity=identity, chunks=tuple(grouped[identity]))
            for identity in sorted(grouped, key=lambda item: (first_index[item], item[1]))
        ]
    return families


def _leaf_results(
    leaf: _SiblingLeaf,
    *,
    seed: RetrievalResult,
    offset: int,
) -> list[RetrievalResult]:
    output: list[RetrievalResult] = []
    for chunk in leaf.chunks:
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "expanded_from_sibling": True,
                "sibling_seed_chunk_id": seed.chunk_id,
                "sibling_offset": offset,
                "sibling_score_provenance": "inherited_seed",
                "char_count": chunk.char_count,
                "token_estimate": chunk.token_estimate,
            }
        )
        output.append(
            RetrievalResult(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                score=seed.score,
                metadata=metadata,
            )
        )
    return output


def expand_siblings(
    results: list[RetrievalResult],
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    """Admit adjacent leaf groups atomically under one deterministic query budget."""
    enabled = (
        knobs.sibling_expansion_enabled
        if knobs is not None
        else settings.sibling_expansion_enabled
    )
    if not enabled or not results:
        return results

    radius = max(
        0,
        knobs.sibling_expansion_radius
        if knobs is not None
        else settings.sibling_expansion_radius,
    )
    max_chars = max(
        0,
        knobs.sibling_expansion_max_chars
        if knobs is not None
        else settings.sibling_expansion_max_chars,
    )
    max_tokens = max(
        0,
        knobs.sibling_expansion_max_tokens
        if knobs is not None
        else settings.sibling_expansion_max_tokens,
    )
    if radius == 0 or max_chars == 0 or max_tokens == 0:
        return results

    seeds = [
        result
        for result in results
        if result.metadata.get("parent_key")
        and result.metadata.get("unit_label")
        and not result.metadata.get("expanded_from_parent")
    ]
    if not seeds:
        return results

    families = _load_families({str(seed.metadata["parent_key"]) for seed in seeds})
    if not families:
        return results

    existing_chunk_ids = {result.chunk_id for result in results}
    reserved_leaves = {
        (str(result.metadata["parent_key"]), str(result.metadata["unit_label"]))
        for result in results
        if result.metadata.get("parent_key") and result.metadata.get("unit_label")
    }
    processed_seed_leaves: set[tuple[str, str]] = set()
    assigned: dict[str, list[tuple[int, _SiblingLeaf]]] = {}
    used_chars = 0
    used_tokens = 0
    operative_only = (
        knobs.retrieval_operative_only
        if knobs is not None
        else settings.retrieval_operative_only
    )

    for seed in seeds:  # fixed post-rerank rank order
        parent_key = str(seed.metadata["parent_key"])
        seed_identity = (parent_key, str(seed.metadata["unit_label"]))
        if seed_identity in processed_seed_leaves:
            continue
        processed_seed_leaves.add(seed_identity)
        family = families.get(parent_key, [])
        try:
            seed_index = next(
                index for index, leaf in enumerate(family) if leaf.identity == seed_identity
            )
        except StopIteration:
            continue

        for distance in range(1, radius + 1):
            for offset in (-distance, distance):  # preceding before following
                sibling_index = seed_index + offset
                if sibling_index < 0 or sibling_index >= len(family):
                    continue
                leaf = family[sibling_index]
                if leaf.identity in reserved_leaves:
                    continue
                if any(chunk.chunk_id in existing_chunk_ids for chunk in leaf.chunks):
                    reserved_leaves.add(leaf.identity)
                    continue
                if operative_only and any(
                    chunk.metadata.get("operability_action") == "hide"
                    for chunk in leaf.chunks
                ):
                    continue
                if (
                    used_chars + leaf.char_count > max_chars
                    or used_tokens + leaf.token_estimate > max_tokens
                ):
                    continue
                assigned.setdefault(seed.chunk_id, []).append((offset, leaf))
                reserved_leaves.add(leaf.identity)
                used_chars += leaf.char_count
                used_tokens += leaf.token_estimate

    if not assigned:
        return results

    output: list[RetrievalResult] = []
    for result in results:
        additions = assigned.get(result.chunk_id, [])
        for offset, leaf in sorted(
            (item for item in additions if item[0] < 0), key=lambda item: item[0]
        ):
            output.extend(_leaf_results(leaf, seed=result, offset=offset))
        output.append(result)
        for offset, leaf in sorted(
            (item for item in additions if item[0] > 0), key=lambda item: item[0]
        ):
            output.extend(_leaf_results(leaf, seed=result, offset=offset))
    return output
