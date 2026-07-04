# ADR-018: Single shared context-selection pipeline for gates, debug, and generation

## Date

2026-07-03

## Status

Accepted

## Plain English

There is now exactly one function that decides what context the system retrieves, and it returns three named stages. The answerability gate, the debug trace, and the generator all read from it — so what you see in debug is what the model actually saw.

## Context

Retrieval post-processing had accreted flags (edge expansion, prefer-operative, parent expansion, dedup) applied ad hoc in `answer_service` and `retrieve`. Gates and debug output could observe a different context than generation consumed ("debug-vs-answer drift"), which made eval forensics untrustworthy. Separately, consolidation (ADR-015) introduced duplicate base/amendment texts that parent expansion could re-inflate, displacing grounded chunks (theft row faith 0.88→0.71).

## Decision

- `app/retriever/context_selection.py` — `select_context(question)` returns `SelectionResult(retrieved, pre_expansion, selected)`:
  - `retrieved`: raw hybrid results
  - `pre_expansion`: post-rerank/edge/prefer-operative — what **gates** score
  - `selected`: post-parent-expansion + dedup — what **generation** receives
- `app/retriever/dedup.py` — conservative consolidated-preference dedup, runs **after** parent expansion, keeps the consolidated variant and carries its metadata (`amended_by`, provenance) onto the survivor.
- `answer_service`, eval, and debug trace all consume the same `SelectionResult`; no adapter re-derives context.

## Alternatives Considered

1. Dedup before parent expansion — expansion re-introduces the duplicates it just removed; ordering is load-bearing.
2. Fix drift by logging generation's context separately — documents the drift instead of eliminating it.
3. Gate on `selected` instead of `pre_expansion` — parent expansion inflates context size and would change gate calibration for no benefit; gates were tuned pre-expansion.

## Reasons

- One selection function makes every eval diff attributable: context-changed rows are exactly the rows where `selected` changed.
- The theft regression's root cause was consolidated Art 309 being displaced by its own pre-consolidation duplicate after expansion — only a post-expansion dedup fixes that.
- Explicit stage names replace implicit flag-application order scattered across call sites.

## Consequences

- New flags must be added inside `select_context` at a deliberate stage, not at call sites.
- `SelectionResult` is now the retrieval contract; the debug trace is trustworthy by construction.
- Judged with ADR-016/017 batch: theft row restored, zero preflight regressions.
