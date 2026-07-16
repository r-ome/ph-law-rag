from difflib import SequenceMatcher

from app.retriever.types import RetrievalResult


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _near_duplicate(a: str, b: str) -> bool:
    left = _norm(a)
    right = _norm(b)
    if not left or not right:
        return False

    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) >= 80 and short in long and len(short) / len(long) >= 0.8:
        return True
    if len(short) >= 80 and long.startswith(short):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.9


def _is_consolidated(result: RetrievalResult) -> bool:
    return result.metadata.get("consolidated") in (1, "1", True)


def _merge_bucket(bucket: list[RetrievalResult]) -> RetrievalResult:
    first = bucket[0]
    metadata = dict(first.metadata)
    metadata["dedup_merged_chunk_ids"] = [result.chunk_id for result in bucket]
    return RetrievalResult(
        chunk_id=first.chunk_id,
        text="\n\n".join(result.text for result in bucket),
        score=first.score,
        metadata=metadata,
    )


def dedup_results(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Conservative same-provision dedup for final context only.

    This runs after structural expansion. It never drops parent- or sibling-expanded
    results and only collapses same-provision duplicates when consolidation metadata
    or strong text similarity makes the duplication explicit.
    """
    by_pid: dict[str, list[RetrievalResult]] = {}
    for result in results:
        pid = result.metadata.get("provision_id")
        if pid:
            by_pid.setdefault(pid, []).append(result)

    drop_ids: set[str] = set()
    replacements: dict[str, RetrievalResult] = {}
    for group in by_pid.values():
        if len(group) < 2:
            continue

        consolidated = [result for result in group if _is_consolidated(result)]
        if consolidated:
            buckets: dict[tuple[str | None, str | None], list[RetrievalResult]] = {}
            for result in consolidated:
                if result.metadata.get("expanded_from_parent") or result.metadata.get(
                    "expanded_from_sibling"
                ):
                    continue
                key = (result.metadata.get("source_id"), result.metadata.get("provision_id"))
                buckets.setdefault(key, []).append(result)
            for bucket in buckets.values():
                if len(bucket) < 2:
                    continue
                replacements[bucket[0].chunk_id] = _merge_bucket(bucket)
                drop_ids.update(result.chunk_id for result in bucket[1:])

            # Sibling additions retain exact leaf identity and cannot participate
            # in dropping an original survivor. Parent additions keep their prior
            # comparator behavior so consolidated law still suppresses duplicate
            # amendment text.
            comparators = [
                result
                for result in consolidated
                if not result.metadata.get("expanded_from_sibling")
            ]
            if not comparators:
                continue
            consolidated_sources = {
                result.metadata.get("source_id") for result in comparators
            }
            for result in group:
                if result.metadata.get("expanded_from_parent") or result.metadata.get(
                    "expanded_from_sibling"
                ):
                    continue
                if _is_consolidated(result):
                    continue
                if result.metadata.get("source_id") not in consolidated_sources or any(
                    _near_duplicate(result.text, kept.text) for kept in comparators
                ):
                    drop_ids.add(result.chunk_id)
            continue

        kept: list[RetrievalResult] = []
        for result in group:
            if result.metadata.get("expanded_from_sibling"):
                continue
            if result.metadata.get("expanded_from_parent"):
                kept.append(result)
                continue
            if any(_near_duplicate(result.text, prior.text) for prior in kept):
                drop_ids.add(result.chunk_id)
            else:
                kept.append(result)

    if not drop_ids:
        return [replacements.get(result.chunk_id, result) for result in results]
    return [
        replacements.get(result.chunk_id, result)
        for result in results
        if result.chunk_id not in drop_ids
    ]
