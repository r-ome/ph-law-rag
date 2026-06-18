# ADR-013: Operative-law preference (provision-level supersession)

## Date

2026-06-18

## Status

Proposed (implemented, opt-in / default off)

> Complements ADR-009 (doc-level edge expansion) at the provision level.

## Plain English

When both an older legal provision and its newer replacement are retrieved, put the newer operative text first. Do not delete the older text, because it may still matter for history or comparison, but do not let it outrank the current law.

## Context

ADR-009 expands base act → amendment at the DOCUMENT level, but legal supersession
is usually PROVISION-scoped: RA 10640 supersedes only §21 of RA 9165; the Family
Code superseded only the marriage Title of the Civil Code. Doc-level suppression
would be wrong (it would discard still-operative provisions of the base act). At
query time, a superseded base provision and its operative replacement can both
survive retrieval, and the stale text may outrank the operative one — a
correctness risk for legal answers.

## Decision

Add a query-time, REORDER-ONLY step: when a superseded base provision and its
operative (amending) replacement are both in the reranked+cut set, move the
superseded chunk to the absolute bottom so operative text outranks it.

- Supersession rules live in a curated map `sources/provision_supersession.yaml`
  (provision-scoped, not SourceConfig — every source is `status=operative` and
  `repeals` edges are empty, so doc-level metadata can't express this).
- `app/retriever/supersession.py` loads rules; `provision_matches` uses a
  word-boundary guard so "Section 21" matches §21/§21(1) but not §210.
- `app/retriever/prefer_operative.py` does the reorder, wired after rerank/edge,
  before parent expansion.
- Downrank, NOT drop: recall is unchanged and the superseded text stays available
  for historical/comparative questions; the intended gain is context_precision.
- v1 = amendment supersession only (RA 10640→RA 9165 §21; RA 11576→BP 129
  §19/§33). Repeal-replace (Civil↔Family marriage/annulment) is DEFERRED to a
  Phase-2 `kind: repeal_replace` because provision numbers don't align across
  codes (Civil Art 85 vs Family Art 45) and need hand-curated pairs.

## Alternatives Considered

1. Drop the superseded provision. Breaks recall and historical questions; a wrong
   match would silently hide law. Rejected — downrank is safe, drop is not.
2. Encode supersession in SourceConfig / manifest edges. Can't express
   provision-scoped or repeal-replace relationships; doc-level is wrong here.
3. Pull in the operative provision when it wasn't retrieved at all
   (Phase 1.5 pull-in). Deferred until downrank-only proves out.

## Reasons

- Provision-scoped is the only correct granularity for PH amendment law.
- Reorder-only is reversible and cannot reduce recall.
- A curated map is honest about scope and keeps a legal parser out of the system.

## Consequences

- Default off (`prefer_operative_enabled=false`). Deterministic eval changed
  context on only 2 rows (both §21) — no measurable benefit yet because the base
  text stays in context anyway (downrank-not-drop). Stays opt-in pending
  Phase-2 repeal-replace, where the larger cross-source wins (annulment) live.
- The jurisdiction regression is cross-source/fragmentation, not operative-law:
  RA 11576 already wins retrieval there, so this lever no-ops on it.
- Requires maintaining `provision_supersession.yaml` as the corpus grows.
