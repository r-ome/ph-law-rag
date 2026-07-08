# ADR-022: Intent router over strategy presets; current_law lane kept on mechanism evidence

## Date

2026-07-08

## Status

Accepted (closes the strategy-router program, R1–R5; supersedes `docs/strategy_router_plan.md`, retired to the devlog)

## Plain English

A small cloud classifier reads each incoming question and picks a retrieval recipe. Only one
recipe differs from the default: questions about amended laws get "prefer the current version
when both old and new text are retrieved." The classifier proved accurate and never activates
the recipe wrongly, but the recipe itself fires so rarely (2 of 81 benchmark questions) that
the eval cannot measure an average win. We kept it anyway because the mechanism is provably
correct and provably harmless — and this ADR records exactly that, instead of claiming a lift
we didn't measure.

## Context

Two LLM-planning experiments (query decomposition, subquery packaging) had already failed
evals: letting an LLM reshape retrieval hurt recall. The router inverts that: the LLM only
*labels* the question; retrieval stays deterministic knob-bundles ("presets"). Five intents
(`default`, `citation_lookup`, `list_or_rule_synthesis`, `amendment_or_current_law`,
`out_of_scope`), mapped many-to-one onto strategies — intent and strategy are distinct
namespaces so label-only lanes can earn promotion from trace data later. Out-of-scope
deliberately routes to default retrieval (the answerability-gate history showed fencing at
retrieval false-fences ~11/70; the generator refuses cleanly instead).

## Decision

- **Preset layer (R2):** `RetrievalKnobs` (7 fields: dense/sparse top-k, rerank_top_n,
  parent expansion, prefer_operative, operative-only filter, consolidated dedup) resolved per
  strategy by a single resolver; the `default` preset passes through Settings so local env
  sweeps keep working. Routed execution never reads preset-owned settings directly.
- **Router (R4):** Haiku (`claude-haiku-4-5`, temp 0) classifies the standalone question
  post-rewrite; the R1 v1 prompt is hash-pinned in `app/retriever/intent_router.py` as the
  single source. Any failure (LLM error, parse error, low confidence) falls back to
  `default` and never raises. `router_enabled` defaults **false** — local surfaces stay
  routerless; cloud/demo surfaces flip it on together with trace logging.
- **One live lane:** `amendment_or_current_law` → `current_law` = default +
  `prefer_operative_enabled=True`. Everything else maps to `default` (label-only).
- **Kept post-R5 on mechanism evidence + no-harm, not measured lift.** The pre-registered
  graduation bar (changed-row recall/precision up, no faithfulness regression) was not met.

## Evidence

- **R1 (81 labeled rows):** Haiku 91.4% strategy-level, amendment precision 1.00, zero false
  `current_law` activations; misses fail safe to default. Local arms: gemma4:e4b ties on
  quality but is blocked by an upstream Ollama memory-estimate bug; qwen3 needs thinking
  tokens (21s); NLI never fires the lane.
- **R3 ($0 stage traces, MiniLM):** `citation_precision` demoted (no mechanism for sparse
  depth to help); `current_law` registered on eval_051 (stale RA 9165 §21 demoted below the
  operative RA 10640 text). Bedrock rerank cuts the operative chunk entirely, so the lane is
  inert under bedrock — it is a **serving-reranker (MiniLM) feature only**.
- **R5 (run `r5_predicted_strategy_20260708_140128`):** router in-pipeline replicated R1
  exactly (74/81, 6/13 amendment, 0 false fires, 0 fallbacks). Changed-context universe =
  2/81 rows. On the one row the shipped router changes (eval_051): faithfulness 0.643→0.800,
  context_recall 0.667→0.333 — mixed, judge-noise-sized at n=1. The router-missed changed
  row (eval_027) was flat, so better router recall buys nothing measurable today. The
  supersession pairs added by the 2026-07-08 coverage audit never fired: both halves of a
  stale/operative pair rarely co-surface post-rerank.

## Alternatives Considered

- **Composer/agent loop** (LLM assembles retrieval plans): rejected up front — two prior
  negative evals on LLM-planned retrieval; deterministic-beats-instruction.
- **Citation-regex pre-route:** rejected; labels showed it misroutes amendment probes that
  mention article numbers (Art 335 / RA 11648).
- **Demote the lane after the unmet bar:** the by-the-book call, declined. The lane fails
  safe, has reproduced mechanism evidence, and costs nothing; the limiting factor is
  evidence volume (it fires too rarely for RAGAS to price), which a rerun cannot fix.
- **Local router seat (gemma3/gemma4):** deferred; haiku on cloud only, local routerless
  until the upstream Ollama estimate bug clears (per-release coexistence tripwire).

## Consequences

- Cloud queries pay ~1s + one Haiku call of routing latency; local behavior is unchanged.
- Any bedrock-reranked eval will (correctly) see `current_law` as inert; R5-style judgments
  must pin MiniLM.
- The keep decision must not be quoted as measured lift. The honest sentence is: "kept on
  mechanism evidence and no-harm; fires too rarely to price."
- Named follow-up lever: make the lane *fire* when it should — stale/operative pair halves
  don't co-surface after rerank, so map coverage alone doesn't activate it.
- Future lanes (fact-pattern/advice for r/LawPH, history/evolution relaxing operability
  hides, lanes for the cross-source watch rows 22/42/46) route through this same mechanism;
  each needs its own eval before mapping to anything but `default`.
