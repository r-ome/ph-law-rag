# ADR-017: Deterministic provision-status hides over ranking or generator instructions

## Date

2026-07-04

## Status

Accepted

## Plain English

When a Civil Code provision has been repealed by the Family Code, we hide the dead text from retrieval outright via a curated override file, instead of trying to rank it lower or telling the LLM to prefer the newer law. Rules that run in code beat rules we ask a model to follow.

## Context

Failure review (6 signatures, converged) showed the dominant fix was superseded Civil Code family-law provisions polluting context. Two prior attempts at softer mechanisms had already failed or underperformed: `prefer_operative` reordering moved chunks but left old text in the prompt, and the later-enacted-preference generator prompt rule A/B'd negative on mistral (weak models don't reliably follow meta-instructions).

## Decision

- `sources/provision_status.yaml` gains a `generated_overrides` block: 269 Civil Code provisions superseded by the Family Code, stamped `hide` with `basis_source_id: family_code`.
- A second OG-provenance block hides Civil Code Title VII (Arts. 216–254, 39 provisions): the official-gazette PDF confirms Art 254's repeal clause includes Title VII, which both lawphil and ChanRobles omit.
- The operability filter stays fail-open: anything not explicitly hidden is served.
- `authority_rank` (constitution-over-statute reordering) was deliberately **not built** — the Const-vs-Art-32 echo appeared in 1/5 traces, not systematic.

## Alternatives Considered

1. Reorder superseded below operative (`prefer_operative`) — leaves both texts in the prompt; blended old/new context is the proven failure mode (ADR-015).
2. LLM self-check / prompt rule — A/B'd negative; instruction-following is not a reliable enforcement layer on a 7B generator.
3. Delete the chunks — loses history-mode answers irreversibly and complicates reindex; hide is reversible metadata.

## Reasons

- Deterministic-beats-instruction: a payload filter is auditable, testable, and cannot be ignored by the model.
- Repeal facts are stable legal facts, ideal for curation; they don't need per-query judgment.
- Web mirrors are unreliable for repeal clauses — load-bearing clauses must be verified against the official-gazette original (both major mirrors dropped Title VII from Art 254).

## Consequences

- History-mode questions about repealed family provisions now lose that text (known trade, replicated across leaf overrides and consolidation).
- The overrides file is corpus-coupled curation that must grow with the corpus; generated blocks need provenance notes to stay auditable.
- Judged result: changed-row faith +0.057 / recall +0.039, theft regression fixed.
