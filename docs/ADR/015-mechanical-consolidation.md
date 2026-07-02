# ADR-015: Mechanical Consolidation

## Status

Accepted.

## Context

Amendment laws are indexed as separate documents. Their inserted Article/Section
chunks carry target-namespace `provision_id` values, which lets retrieval find
new law, but it can still put old and new wording in the same prompt. The leaf
override rollout proved that blended old/new context is the failure mode: hiding
the superseded §21 leaves fixed the current-law witness answer, while leaving
history-mode questions as a separate product problem.

Milestone #2a added read-only per-provision timelines. Those timelines make a
small, mechanical splice bucket possible without model judgment or fuzzy text
alignment.

## Decision

At `raglab reindex` time, build a consolidation plan from the pre-reindex chunk
snapshot. A provision qualifies only when it has:

- a base entry
- exactly one insertion entry
- no `provision_partial` flag
- length ratio from 0.7 to 1.5
- no matching `provision_status.yaml` override
- a preflight check where `provision_spans()` recomputes the same partial flag
  from the amendment normalized file

Everything else is skipped and reported: partials, chains, ratio outliers,
no-base insertions, override collisions, and preflight mismatches.

For qualifying provisions, splice the amendment's restated unit into the base
document text before child chunking and parent extraction. The spliced provision
gets inline provenance:

```text
[as amended by <official number>, approved <date>]
```

Base chunks also carry payload metadata (`consolidated`, `amended_by`,
`amendment_official_number`, `amendment_approval_date`,
`consolidation_basis`). The duplicate amendment insertion chunks are retained in
SQLite/Qdrant but stamped `operability_action: hide`,
`provision_status: consolidated`, and `operability_basis: consolidated`.

Full reindex enforces a coherence check after all sources are processed. A
doc-scoped reindex that targets either side of a consolidated pair auto-expands
to reindex both base and amendment documents.

## Rationale

No model is needed for this bucket because the hard inputs are already computed:
the target provision identity comes from amendment-aware chunking, and complete
vs. partial replacement comes from the partial marker plus a length-ratio sanity
band. The plan still treats those inputs as fallible: stale partial flags once
made RA 10364 §6 look safe when it was not, so dry-run preflight recomputes the
flag from normalized text before splicing.

Duplicate amendment chunks are hidden because leaving them live recreates the
old/new context arena that consolidation exists to remove. Originals are not
deleted; the filter remains fail-open for anything not explicitly hidden.

Chains and partials are excluded because they need chronological patching or
leaf-level semantics. Guessing there can silently destroy operative law.

## Consequences

Consolidation is a chunk-level derivation. It does not modify raw or normalized
files and does not create `document_versions` rows. Sync remains unaware in v1;
operators apply consolidation through `raglab reindex`.

This does not fix stale cross-references inside otherwise operative law, such as
RA 7610 text that still points to old RPC Article 335. That requires an
annotation/jurisprudence layer or generator-side current-law preference.
