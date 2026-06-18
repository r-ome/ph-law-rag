# ADR-014: Deterministic generation and judge-noise control in evals

## Date

2026-06-18

## Status

Accepted

## Plain English

Make evaluation comparisons fair before trusting them. Generation must be deterministic, repeated judge scores should be cached, and retrieval experiments should be judged on the questions whose retrieved context actually changed.

## Context

A/B comparisons of retrieval changes were untrustworthy. Two noise sources
swamped the signal:

1. **Generation was never greedy.** `llm_client.py` set `temperature: 0` at the
   request top level, but Ollama `/api/chat` reads sampling params from an
   `options` object — so the top-level value was ignored and generation ran at
   Ollama's default (~0.8). The same question produced different answers each run.
2. **The RAGAS judge (haiku) is itself stochastic.** Even with identical answer
   and context, faithfulness swung -0.25..+0.29 on ~11 of 68 rows. This is the
   dominant remaining noise floor once generation is fixed (~±0.02-0.03 on
   whole-run aggregate faithfulness).

With both present, a retrieval change touching ~11/70 questions was invisible
under aggregate noise, leading to at least one wrong verdict (parent expansion
first judged a faithfulness regression, later proven to be generator noise on
untouched control rows).

## Decision

Make evals trustworthy via three controls:

1. **Deterministic generation** — move sampling params into Ollama's `options`
   object and set `seed` (temperature 0 + seed:42). Generation is now byte-stable
   across runs for unchanged inputs.
2. **RAGAS score cache** — cache judge scores keyed on a hash of the eval sample
   (question, answer, contexts, reference) plus the scoring configuration (metric
   names, judge model, embedding model, RAGAS version, scorer version); reuse on
   unchanged rows. Collapses judge noise to zero on rows whose inputs and config
   didn't change, and cuts cost.
3. **Read changed rows, not aggregates** — for a retrieval-only change that fires
   on a minority of questions, diff the retrieved contexts between runs and report
   metrics only on rows whose context actually changed. Treat identical-context
   rows as the judge-noise floor and ignore them.

## Alternatives Considered

1. Average N judge passes per row. Reduces but does not eliminate judge noise;
   N× the cost. The cache is cheaper and exact on unchanged rows.
2. Control judge determinism (haiku temp/seed). Not reliably exposed through
   RAGAS; deferred. The cache sidesteps it for unchanged rows.
3. Keep reading whole-run aggregates with bigger n. The eval set is fixed at 70;
   the minority-fire problem is structural, not a sample-size problem.

## Reasons

- The temperature bug was a real correctness defect, not a tuning choice.
- The cache is the structural fix for judge non-determinism (ADR-005 named the
  problem; this resolves it for the common A/B case).
- Changed-row reading is the only method that isolates a minority-fire retrieval
  change from generator/judge noise.

## Consequences

- Determinism changes answers, so all pre-fix baselines are non-comparable.
  Retired them; `det-baseline` (all flags off: faith 0.809 / prec 0.607 /
  rec 0.742 / abst 64/70) is the new control.
- The cache invalidates automatically when any keyed field changes (judge/
  embedding model, metric set, RAGAS version, scorer version, or the sample
  itself); a judge-prompt change not reflected in those fields needs a manual
  clear plus a scorer-version bump.
- Aggregate faithfulness still carries a judge-noise floor on rows not covered by
  the cache; the changed-row rule remains required for honest verdicts.
- Methodology lesson recorded for future levers: judge retrieval-only changes on
  context-changed rows, never whole-run aggregates with a stochastic generator.
