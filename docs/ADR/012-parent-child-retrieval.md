# ADR-012: Parent-child (parent-expansion) retrieval

## Date

2026-06-18

## Status

Accepted

> Builds on the provision-aware child chunks of ADR-007.

## Plain English

Keep small child chunks because they help the retriever find exact provisions, but give the model the full parent section when several children from the same section are relevant. This keeps precision for search while restoring enough surrounding text for complete legal answers.

## Context

Provision-aware (enumeration) chunking (ADR-007) raises per-chunk precision by
making each offense/clause its own small chunk, so a buried provision (e.g.
Cybercrime §4(c)(4) online libel) becomes findable. But it LOSES recall on
list-spanning questions: an enumeration that must be answered whole (drug
chain-of-custody §21, civil jurisdiction §19/§33) gets fragmented across child
chunks, and the cutoff keeps only some fragments — so the LLM sees an incomplete
list. Three eval rounds confirmed finer chunks are a recall lever in BOTH
directions.

## Decision

Index fine child chunks for retrieval precision, but RETURN the parent
section as context. After rerank and cutoff, expand each surviving child to its
parent unit when enough siblings are present, merging in rank order under a
character budget.

- Child chunks carry a `parent_key`; parents are extracted at index time and
  stored in a `chunk_parents` table (migration 2).
- Expansion runs post-rerank in `app/retriever/parent_expansion.py`, last in the
  retrieval pipeline — after edge expansion and operative-law reordering
  (ADR-013).
- Eligibility: expand only when `min_children` (default 2) surviving children
  share a parent; cap merged context at `max_chars` (default 8000) so a single
  large section can't flood the prompt.
- Reindex to populate parents: `raglab reindex` rebuilds from on-disk normalized
  text (no fetch / no new version).

## Alternatives Considered

1. Revert to coarse chunks. Loses the buried-provision win (§4(c)(4) goes back to
   rank ~23). Rejected — both query classes matter.
2. LlamaIndex `AutoMergingRetriever`. Same idea; a hand-rolled rank-order merge
   gave tighter control over the budget cap and eligibility rule for this corpus.
3. Just widen the rerank cutoff (margin6/top8, already shipped). Partially
   re-gathers fragments but doesn't guarantee a whole section; complementary, not
   a replacement.

## Reasons

- Captures both query classes at once: precise children for lookup, whole parents
  for synthesis.
- Reuses the existing rerank/cutoff controls; expansion only adds context for
  already-surviving children.

## Consequences

- Default-on (`parent_expansion_enabled=True`). Verdict: it changes context on
  11/70 questions (fragmented sections), leaving 59 untouched. On the fired rows,
  context-recall improved (+0.136) with no meaningful faithfulness regression;
  whole-run faithfulness moved slightly up. See ADR-014 for why this is judged on
  changed rows, not the whole-run aggregate.
- Adds migration 2 (`chunk_parents`) and a reindex requirement.
- Does NOT fix cross-source ambiguity (competing statute versions) — expansion
  merges a child to its OWN parent, not across sources. That is ADR-013's job.
- Budget cap means very large sections still return as leaves, not a full parent.
