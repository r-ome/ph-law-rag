# ADR-009: Edge-aware (multi-hop) retrieval

## Date

2026-06-13

## Status

Proposed

## Context

PH laws reference each other: an amendment changes a base act, an IRR implements a statute. Plain hybrid retrieval treats every document in isolation, so a query can surface a base act whose provision was later amended (stale answer) without ever seeing the amendment. The relationships exist in the source manifest (`amends`, `implements`, `supersedes`, `repeals`) but are never used at retrieval time.

## Decision

Add a single-hop expansion step between rerank and context-building. After the seed retrieval, follow **expansion edges** (`amends`, `implements`, both directions) from each seed document to its operative neighbors, run a vector search scoped to each neighbor, merge, and re-rank the combined set once.

- Edges are read from the manifest at query time (in-memory graph, no DB schema change).
- Doc-level only — edges point document→document. Partial (article-range) supersession is out of scope.
- `supersedes`/`repeals` are **not** expansion edges; whole-doc suppression is already handled by the status filter (ADR's status-aware retrieval).
- The final re-rank keeps `rerank_min_score` / `rerank_top_n` in charge, so edge-pulled chunks must earn their place.

## Alternatives Considered

1. No multi-hop — rely on the user to ask follow-ups. Misses amendments silently.
2. Persist edges to a DB table populated at sync. More infra for an 18-row graph that already lives in the manifest.
3. Pull all chunks of a neighbor document. Floods context (e.g. the whole Civil Code); scoped vector search keeps only query-relevant neighbor chunks.

## Reasons

- Reflects how legal authority actually composes (base act + amendment) without the user knowing to ask.
- Manifest-as-source-of-truth, fail-open, no schema migration.
- One re-rank over the merged set means the existing precision controls govern the result.

## Consequences

- The corpus now has real amendment probes (RA 11576→BP 129, RA 10951→RPC, RA 10640→RA 9165); future IRRs can add `implements` probes once fetchable sources are available.
- `dense_retriever` needs an optional `source_id` filter for scoped neighbor search.
- Edge-pulled sources should be labeled (e.g. "amends …") so citations show the relationship.
- One hop only; transitive chains (A→B→C) are not followed.
