# ADR-019: Sync boundary refactor — one modular-monolith seam, not microservices

## Date

2026-07-04

## Status

Accepted

## Plain English

We considered splitting sync/indexing into separate services and killed the idea after three review rounds. Instead we cut clean module boundaries inside the monolith — a sync orchestrator, a shared source-metadata builder, a runtime health module — and enforce the layering with import tests.

## Context

The sync path had grown tangled: `ingestion/sync.py` orchestrated fetching, versioning, indexing calls, health checks, and metadata reconciliation. A microservices proposal was reviewed three times and rejected on two hard findings: the BM25 full rebuild is a global operation that races across service boundaries, and the inline `index_document` call inside sync is load-bearing for the incremental guarantee (index exactly what changed, when it changed). The plan was scaled down to one boundary-cleanup PR.

## Decision

Shipped in 67cb704:

- `app/sync_service.py` — sync orchestration (per-source loop, counts, `sync_runs` row, reconcile-action dispatch); `app/ingestion/sync.py` reduced to per-source `ingest_source` (fetch/parse/normalize/version).
- `app/source_metadata.py` — single `build_source_metadata` shared by sync, reindex, and retrieval instead of three ad-hoc constructions.
- `app/runtime/health.py` — health checks out of the API adapter.
- `tests/unit/test_import_boundaries.py` — AST-based tests forbidding e.g. `app.ingestion` importing indexing/retriever/adapters; the architecture is enforced, not documented.

## Alternatives Considered

1. Microservices split (sync svc / index svc) — killed: BM25 rebuild race, load-bearing inline index call, and no scaling need at 45 sources.
2. Leave as-is — `sync.py` at 200+ lines of mixed concerns was where every regression hid.
3. Docs-only layering rules — unenforced boundaries erode; import tests are cheap and permanent.

## Reasons

- Consistent with the M5 modular-monolith decision; a seam gives future extraction points without paying distributed-systems costs now.
- Import-boundary tests convert an architecture diagram into a failing test.
- Metadata construction had already caused a real bug class (stale `status` filter) — one builder closes it.

## Consequences

- Any future service extraction starts from `sync_service.py`'s seam; the BM25 global-rebuild constraint must be solved first (per-store or rebuild-token design).
- New cross-layer imports fail CI immediately.
- `ingest_source` is now independently testable without indexing side effects.
