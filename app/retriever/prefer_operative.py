"""Operative-law preference (post-rerank, reorder-only, off by default).

When a superseded base provision and its operative (amending) replacement are BOTH in
the reranked+cut result set, move the superseded chunk to the bottom of the list so the
operative text outranks it in the prompt. Reorder-only: nothing is dropped, so recall is
unchanged and the old text stays available; the intended gain is context_precision.

Query-time only — reads each leaf's source_id + unit_label against the supersession map;
no index metadata, no re-index. Runs after rerank/cutoff and before parent expansion so
the operative-first order is preserved when parents expand.
"""

from app.config import settings
from app.retriever.types import RetrievalResult
from app.retriever.supersession import load_supersessions, provision_matches


def prefer_operative(results: list[RetrievalResult]) -> list[RetrievalResult]:
    if not settings.prefer_operative_enabled or not results:
        return results
    rules = load_supersessions()
    if not rules:
        return results

    covered: set[int] = set()
    for rule in rules:
        operative_present = any(
            r.metadata.get("source_id") == rule.operative_source_id
            and provision_matches(r.metadata.get("unit_label"), rule.operative_provisions)
            for r in results
        )
        if not operative_present:
            continue
        for i, r in enumerate(results):
            if (
                r.metadata.get("source_id") == rule.base_source_id
                and provision_matches(r.metadata.get("unit_label"), rule.base_provisions)
            ):
                covered.add(i)

    if not covered:
        return results

    # stable: keep original order for everything else, append superseded chunks at the bottom
    return [r for i, r in enumerate(results) if i not in covered] + [results[i] for i in sorted(covered)]
