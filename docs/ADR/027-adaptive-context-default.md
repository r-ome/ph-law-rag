# ADR-027: Adaptive final-context packaging as the retrieval default

## Date

2026-07-17

## Status

Accepted

## Plain English

The pipeline now trims the final context with a deterministic, score-agnostic
selector before generation. It keeps the highest-value legal evidence, preserves
atomic sibling groups, and uses wider caps when structural signals indicate
uncertainty or synthesis. The result is materially smaller prompts without a
measured quality regression on the sealed holdout. Rollback is one flag:
`adaptive_context_enabled=false`.

## Context

Phase 3 sibling expansion improved evidence recall by adding nearby enumeration
leaves, but it also made prompts larger. Phase 4 tested whether the final
post-dedup context could be packaged more selectively while preserving answer
quality.

The selected contract-v2 policy is intentionally simple and deterministic:

- floor: 4 chunks;
- caps: 7 ordinary, 11 coverage-uncertain, 11 synthesis/multifacet;
- token target: 2,400 rendered-char/4 tokens;
- stabilization: stop after two consecutive non-novel bundles;
- score-agnostic selection over explicit selector-consumed fields only;
- seed-centered sibling bundles remain atomic.

The caps were tuned on regression/dev evidence, so graduation required the
sealed 30-row holdout release gate before any default flip.

## Decision

- `adaptive_context_enabled` defaults to `true`.
- The default flows through `Settings → RetrievalKnobs → AnswerPolicy → active
  config identity → trace diagnostics`.
- The seam runs after post-expansion dedup and before context rendering.
- The selector records the semantic packaging-pool hash, full diagnostic pool
  hash, structural signals, cap, stop reason, rendered tokens, and counts.
- Rollback is a single config/env flag:
  `adaptive_context_enabled=false`.
- No schema-1.2 publication or explicit `packaging_pool` artifact is included in
  this decision; that remains a follow-up.

## Evidence

### Equivalence and bridge

- CP-A0 locked sentinel probe: 5/5 full-hash matches against the Phase 3 frozen
  sibling baseline.
- CP-A2.b pure selector-logic equivalence: 131/131 non-holdout rows matched the
  sealed `phase4-adaptive-context-v2-minilm` artifact.
- CP-A2.c live-on semantic equivalence: 131/131 non-holdout rows matched by
  chunk ID/order, text, and selector-consumed fields.
- A score-inclusive mismatch observed during diagnosis was attributable to
  volatile `_retrieval_scores.dense_score`, which the selector does not read.
  Stage B therefore used one retrieval pass per row and derived both arms from
  the same post-dedup pool, making the semantic pool invariant structural.

### Non-holdout CP-A3

The single-pass dev run `phase4-single-pass-dev-cp-a3-20260717-1010` reproduced
the offline v2 generation evidence exactly:

- rows: 131;
- common answered cohort: 115;
- faithfulness Δ: -0.008280970238260799;
- context recall Δ: +0.002898550725217386;
- false abstentions: 5 → 4;
- rendered-token reduction: 0.1164511165838384;
- changed contexts: 66.

The paired comparator passed identity, scoring-identity, storage-consistency,
semantic-invariant, and non-disclosure checks.

### Holdout Stage B

The sealed holdout was read once, aggregate-only, with exactly one holdout
ledger metric-read entry. No holdout row content was inspected or disclosed.

Run: `phase4-single-pass-holdout-stage-b-20260717-1200`.

| Gate | Result | Status |
|---|---:|---|
| Common answered cohort | 29 / 30 | informational |
| Faithfulness Δ | +0.022550629444827552 | pass |
| Context recall Δ | -0.005747126437931072 | pass |
| False abstentions | 1 → 1 | pass |
| Rendered-token reduction | 0.1376842483117582 | pass |

Execution aggregates:

- changed contexts: 10 / 30;
- candidate caps: 22 rows at cap 7, 8 rows at cap 11;
- candidate stop distribution: cap=2, exhausted=21, stabilized=5,
  token_target=2;
- signal activations: coverage_uncertain=3 and synthesis_detected=5 in both
  arms;
- labeled synthesis holdout evidence remains n=4.

The final paired verdict was `eligible_for_release_decision`.

## Caveats

- Small N: the holdout has 30 rows and 29 common answered rows, so one row can
  materially move the deltas.
- Context recall dipped on holdout by -0.005747126437931072. This is inside the
  locked non-inferiority band, but it is the one primary metric moving the wrong
  direction.
- The holdout has no OOS or ambiguous rows. The out-of-scope moat remains
  validated on non-holdout OOS rows, not by this holdout.
- The multi-facet/synthesis path is weakly evidenced: labeled synthesis holdout
  n=4, though cap 11 activated structurally on 8 holdout rows.
- The mechanism changed 10/30 holdout contexts, less than the 66/131 dev
  changed-context rate. Anti-inert holds, but many holdout pools were already at
  or below cap.

## Consequences

- Default prompts become smaller when the post-dedup pool exceeds the structural
  cap; the measured holdout mean reduction was 13.77%.
- The mechanism adds selector diagnostics to traces and active config identity.
- The rollback path is operationally simple: set
  `adaptive_context_enabled=false`.
- If production behavior later suggests evidence trimming is harming recall,
  revert the flag first and investigate on dev/fresh evaluation data rather
  than reopening or probing holdout rows.

## Artifacts

- Non-holdout dev aggregate:
  `data/eval_results/phase4_paired/phase4-single-pass-dev-cp-a3-20260717-1010.json`
- Holdout aggregate:
  `data/eval_results/phase4_paired/phase4-single-pass-holdout-stage-b-20260717-1200.json`
- Holdout ledger:
  `data/eval_results/holdout_aggregate_reads.jsonl`
- Program record:
  `docs/retrieval_strategy_review.md` (Phase 4 holdout validation); retired
  2026-07-18, full text in git history at `44b3c3a`, summary in
  `docs/project_plan.md` (program record section)
