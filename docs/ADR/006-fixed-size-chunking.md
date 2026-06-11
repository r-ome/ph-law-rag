# ADR-006: Fixed-size chunking with overlap

## Date

2026-06-11

## Status

Accepted

## Context

The corpus needs splitting before embedding. A uniform strategy that works on every document — hierarchy or not — ships a working retrieval baseline without per-document parsing.

## Decision

Chunk by fixed size with overlap using LlamaIndex `SentenceSplitter` (`chunk_size=256`, `chunk_overlap=32`), attaching source metadata to each chunk.

## Alternatives Considered

1. Document-level / structure-aware chunking. Deferred because it needs per-document boundary parsing and not all documents share the full hierarchy. See ADR-007.

## Reasons

- Simple and uniform: one splitter for every document.
- Ships a measurable baseline fast; overlap reduces context loss at boundaries.

## Consequences

- Chunks can split mid-section and lose structural context beyond the attached metadata.
- Revisit with document-level chunking (ADR-007) if evals show retrieval suffering. Known, deliberate deferral.
