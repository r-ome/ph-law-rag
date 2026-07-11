# ADR-025: Gemma4 e4b as the local generator default

## Date

2026-07-11

## Status

Accepted; supersedes ADR-010 for the local generator default

## Plain English

The local pipeline now answers with `gemma4:e4b` by default. This makes a clean
checkout run the same generator as the newly locked 131-row Qwen baseline instead
of relying on a hidden `.env` or Docker override. Mistral remains selectable for
historical comparisons but is no longer the default answer model.

## Context

ADR-010 selected Mistral when the local corpus, prompts, retrieval stack, and
model options were materially different. Gemma4 later became the generator used
for the lawyer-style prompt work, the Qwen embedding graduation A/B, and the
expanded 131-row baseline. Until this decision, that operational choice existed
only as a developer `.env` value and a Docker override while `Settings` and named
policy profiles still defaulted to Mistral.

That split made the phrase "local default" ambiguous: a fresh checkout ran
Mistral, while the published current baseline measured Gemma4. The default should
identify the measured stack.

## Decision

- `Settings.llm_model` defaults to `gemma4:e4b`.
- The named-profile base used by `eval`, `cloud`, `cascade`, and
  `local-cascade` also starts from `gemma4:e4b`; profiles may still replace it
  explicitly.
- `.env.example`, local setup instructions, README, and the project plan name
  Gemma4 as the local generator.
- The Docker development override for Gemma4 is removed because it is now
  redundant.
- Cloud deployment remains explicitly pinned to Claude Haiku through its own
  environment configuration. This ADR does not change the deployed cloud
  generator.
- Query-planner and answerability-judge model defaults remain Mistral. They are
  separate experimental roles and were not part of this generator promotion.

## Evidence

The standing artifact
`gemma4-e4b_qwen06-baseline-131_20260711_104509` ran the current local stack over
81 regression and 50 dev rows with the 30-row holdout unopened:

| Metric | Locked baseline |
|---|---:|
| Correct abstention decisions | 123/131 (93.9%) |
| Faithfulness | 0.8998 |
| Answer relevancy | 0.7701 |
| Context precision | 0.6868 |
| Context recall | 0.8330 |

Generation completed all 131 rows with zero pipeline errors and 13.31-second
median end-to-end latency. Anthropic Haiku judged the scored answers with the
standing RAGAS cache and Nomic judge embedding.

This is an operational graduation and baseline-alignment decision, not a claim
that a matched 131-row Gemma4-versus-Mistral A/B proved a quality delta. No such
131-row control exists. Earlier generator screens established the practical
direction, while this run establishes that the selected current stack is stable
and measurable.

## Alternatives Considered

1. Keep Mistral as the code default while using Gemma4 through `.env` — rejected:
   a hidden override makes fresh-checkout behavior differ from the locked
   baseline and repeatedly confounds eval setup.
2. Run a new 131-row Mistral control before changing the default — not required
   for alignment. It would quantify the generator delta but would not change
   which stack the user selected as the default; it remains a valid future A/B.
3. Make Claude Haiku the universal default — rejected: local-first development
   must remain usable without paid generation. Cloud remains explicitly pinned
   to Haiku.

## Consequences

- Fresh local configuration requires `ollama pull gemma4:e4b` and more memory
  than Mistral.
- Unset `llm_model` now reproduces the generator used by the 131-row baseline.
- Old Mistral artifacts remain historical controls, not directly comparable to
  the expanded baseline without a matched rerun.
- Closed experimental profiles such as `local-cascade` inherit Gemma4 as their
  default seat; they remain non-graduated and off unless explicitly selected.
