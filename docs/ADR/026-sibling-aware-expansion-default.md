# ADR-026: Sibling-aware expansion as the retrieval default

## Date

2026-07-16

## Status

Accepted

## Plain English

When search finds one item of a legal enumeration — say Article 1403(2)(d) —
the pipeline now also pulls in the immediately adjacent items of the same list,
such as (c) and (e). Legal enumerations are meant to be read together: the
provision that actually answers the question is often the neighbor of the one
the ranker picked. The addition is strictly bounded (one leaf on each side,
about a page of extra text per question at most), never removes or reorders
anything already retrieved, and measurably improved answer evidence without
hurting precision or faithfulness.

## Context

Parent expansion (default since 2026-06-18) swaps in a whole section when two
or more of its leaves survive reranking, but it never fires when exactly one
leaf survives — the common failure shape identified in the retrieval strategy
review as "sibling fragmentation" (`eval_053`: context selected Article
1403(2)(d) while the applicable rule was its sibling 1403(2)(e)). Three prior
attempts to fix paraphrase-style misses by manipulating the query (decomposition,
subquery packaging, legal-query separation) all failed their matched runs. This
mechanism instead changes what surrounds a surviving result, structurally and
deterministically, with no LLM in the loop.

## Decision

- `sibling_expansion_enabled` defaults to `true`. Radius 1 leaf on each side;
  global per-query budgets of 3,000 added characters and 750 estimated tokens;
  admission is leaf-atomic, first-fit, in seed rank order, preceding before
  following, inserted in document order around the seed.
- Sibling identity is `(parent_key, unit_label)` read from chunk metadata;
  families load from SQLite ordered by `chunk_index`. No migration, no reindex.
- The stage runs after parent expansion and before the expanded trace snapshot
  and dedup. Sibling-expanded results are exempt from consolidated dedup
  merge/drop so leaf identity survives to citations and metrics.
- Existing survivors are never moved, re-budgeted, or duplicated; hidden leaves
  (`operability_action=hide`) are excluded when operative-only retrieval is on.
- The `current_law` preset is unchanged (its knobs stay pinned as graduated);
  the `sibling_aware` preset remains registered for matched comparisons.

## Evidence

Matched A/B on the 131 non-holdout rows, frozen MiniLM retrieval bundles,
deterministic `gemma4:e4b` replay (zero answer changes on the 67
unchanged-context rows):

- Retrieval: exact-leaf coverage 0.5769 → 0.6154; zero baseline chunks or
  targets lost; 64 contexts changed, all additively (167 chunks); mean context
  +9.7%; latency +1.9%; per-row budget maxima 2,977 chars / 746 tokens.
- Generation: context recall 0.833 → 0.857; precision flat at 0.687;
  faithfulness 0.900 → 0.894 (noise-sized); false abstentions 7 → 5
  (`eval_056`, `eval_057` — both documented false-abstention rows — now
  answer); out-of-scope moat unchanged.
- `eval_053` fixed end-to-end: context recall 0 → 1.0, faithfulness 0.8 → 1.0.
- Known cost mode (watch row): `eval_129` faithfulness 0.83 → 0.42 — the
  generator drifted onto sibling-added ADR §11 exception text (its context
  recall rose 0.5 → 0.8). One genuine regression against ~8 genuine
  improvements on changed rows.

The radius-1 eligibility census found only 1 of 7 missed exact-leaf rows
recoverable, so the predeclared 80% recovery gate ran in its descriptive
small-N regime; graduation rests on the no-regression gates plus the
generation A/B above.

## Consequences

- Serving contexts grow ~10% on the ~half of questions where the mechanism
  fires; budget caps bound the worst case.
- Nearly all additions are non-target-bearing by construction (symmetric
  radius); the A/B shows the generator tolerates this, but `eval_129` is the
  canary — revisit if drift-onto-sibling-text recurs.
- Rollback is one flag: `sibling_expansion_enabled=false` restores the prior
  baseline exactly (additive-only was verified per row).

## Artifacts

- Implementation `d0f9df5`; retrieval gates `b7bb9f1`.
- Sealed bundles: `data/eval_results/runs/2026-07-15/phase2-original-minilm`
  (baseline), `data/eval_results/runs/2026-07-16/phase3-sibling-aware-minilm`.
- Generation runs: `.../2026-07-16/phase3-gen-{baseline,sibling}-gemma4`;
  diff `data/eval_results/diffs/diff_phase3-gen-sibling-gemma4.md`.
- Program record: `docs/retrieval_strategy_review.md` (Phase 3); retired
  2026-07-18, full text in git history at `44b3c3a`, summary in
  `docs/project_plan.md` (program record section).
