# ADR-007: Document-level (structure-aware) chunking

## Date

2026-06-11

## Status

Proposed

> Not yet implemented; current code uses fixed-size chunking (ADR-006). Supersedes ADR-006 if built.

## Context

PH law mostly follows a hierarchy (Book → Title → … → Clause). Fixed-size chunking (ADR-006) can split mid-Section, so a chunk may lose its structural context. Chunking along the hierarchy keeps chunks whole and improves precision for citation-style queries.

## Decision

Chunk along document structure (Section / Article boundaries) instead of a fixed token count, carrying the structural position (Article/Section) in metadata.

## Alternatives Considered

1. Fixed-size chunking with overlap (current ADR-006). Simpler, but can split mid-section.
2. Hybrid: structure-aware boundaries with a fixed-size fallback for documents that lack the hierarchy or have oversized sections.

## Reasons

- Keeps chunks whole, reducing retrieval noise.
- Structural metadata enables precise, citation-style answers — a good fit for a legal corpus.

## Consequences

- Needs per-document boundary parsing plus a fallback for non-hierarchical docs.
- More complex than fixed-size; variable chunk sizes may need a secondary split.
- Measure against the ADR-006 baseline with evals before accepting.
