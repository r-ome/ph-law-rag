# ADR-007: Document-level (structure-aware) chunking

## Date

2026-06-11 (proposed) · 2026-06-13 (accepted)

## Status

Accepted

> Implemented in `app/indexing/chunker.py`. Supersedes ADR-006.

## Plain English

Chunk laws by their actual legal structure instead of blindly cutting text every few tokens. A whole article or section is usually a better retrieval unit than an arbitrary text slice, and oversized enumerations can still be split into smaller provision-level chunks when needed.

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

## Implementation notes

Built in `app/indexing/chunker.py`. Key design decisions that emerged during implementation:

- **Marker grammar (line-start regex).** UNIT markers (`Article N`, `Section N`) begin a chunk; PARENT markers (`Book`/`Title`/`Chapter`, roman-numeral `ARTICLE` constitution divisions, `Rule N`) only update `structure_path` context and are never unit boundaries themselves.
- **Three routing rules.** The manual `structure` hint wins: `prose` → sentence-split; `hierarchical` → structural if any units parsed, else prose fallback; `auto` → structural only if units `_looks_structural`.
- **Cautious auto-detect.** `_looks_structural` requires a monotonic ascending run of ≥ `MIN_UNITS` (5), not just many markers. This defeats prose decisions that *quote* scattered sections out of order (e.g. "Sec. 16" then "Sec. 15").
- **Per-chunk metadata.** Each structural node carries `is_structural, unit_type, unit_number, unit_label, structure_path`. This metadata powers the pinpoint citation locator in `app/retriever/context_builder._locator` (e.g. "Article III, Section 12"). Prose nodes carry `is_structural=False`.
- **Oversized units → provision-aware sub-split (`_enumeration_nodes`).** A unit longer than ~`chunk_size * 4` chars is sub-split. When the unit contains a letter/number enumeration `(a)/(b)/(c)`, `(1)/(2)/(3)`, the splitter cuts on those markers so each provision (e.g. §4(c)(4) online libel) becomes its own node, instead of a blind character-count split that buries a provision as a minority passenger in a multi-offense chunk. Markers are classified by SEQUENCE/CONTEXT, not glyph shape (a stack of active enumeration frames; `(i)` resolves alphabetic vs roman by which frame it continues). A marker is split iff its kind and its parent's kind are both non-roman (roman series stay glued to their parent item). The child's `enum_path` (e.g. `(c)(4)`) is prefixed into the chunk text as a breadcrumb so fragments stay self-contained. Units with no enumeration fall back to `SentenceSplitter` (`part_index`).
- The size gate is load-bearing: prose never reaches this branch, and global sub-splitting would explode the index (the Civil Code alone has ~1081 line-start enum markers).
- **Preamble.** Text before the first unit (statute title / enacting clause) is emitted as prose so nothing is dropped.

### Known limitations (deferred)

- Article *title* (e.g. "Bill of Rights" for Article III) is not captured in metadata — only the unit label and parent path.
- No cross-unit edge awareness (amends/repeals/supersedes) at chunk level; that lives in the source manifest.
- Provision-aware splitting raises precision but fragments enumerations that must be answered whole; that recall loss is recovered at query time by parent expansion (ADR-012), which returns the parent section as context.
