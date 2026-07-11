# ADR-024: Qwen3-Embedding-0.6B (instruct) as the local embedding default

## Date

2026-07-11 (decision 2026-07-10; baseline re-locked 2026-07-11)

## Status

Accepted

## Plain English

We swapped the local search brain. The old embedder matched questions to law text mostly by
shared vocabulary, so a question phrased in everyday language ("my landlord kicked me out")
could miss the provision written in legalese. The new one accepts a one-line instruction
telling it what the query is for, which closes part of that lay-to-legal gap — the exact
failure mode our paraphrase eval rows measure. Answer quality on everything else stayed flat,
the out-of-scope fence did not erode, and the extra latency is mostly the laptop juggling one
more resident model, not the embedder being slow.

## Context

`nomic-embed-text` (768-dim, symmetric) had been the local default since M3. The retrieval
diagnosis program showed chunk-level paraphrase recall was the weakest retrieval cell, and the
embedder was the last untested stage of the local stack (k30 and reranker had both been
sweep-tested). Qwen3-Embedding-0.6B is instruction-aware: the query side can be prefixed with
a task instruction while documents are embedded raw — an asymmetry designed for exactly this
query-document register mismatch.

## Decision

- `_EMBEDDING_BACKEND_DEFAULTS["ollama"]` → `qwen3-embedding:0.6b`, 1024-dim;
  `qdrant_collection` default → `ph_law_qwen06`. The legacy 768-dim `ph_law` collection is
  left untouched (rollback = four env lines: model, dim, collection, blank instruction).
- Asymmetric embedding: documents embed raw (`embed_texts`); queries are prefixed
  `Instruct: {embedding_query_instruction}\nQuery: {q}` in
  `dense_retriever._format_embedding_query`. The default instruction: "Given a Philippine law
  question, retrieve the statutory provisions and jurisprudence that answer it."
- `ragas_embedding_model` stays `nomic-embed-text` — the judge's instrument is pinned
  independently of the system under test.
- The cloud/eval-parity embedding backend (`bedrock` → Titan v2, 1024-dim) is unchanged.
- Committed c4b25c0. `.env` reproduces the graduated local stack (gemma4:e4b + qwen06 +
  MiniLM); code defaults for generator/reranker stay per ADR-021.

## Evidence

- Judged A/B (81 rows, single-var swap vs nomic, same gemma4:e4b + MiniLM + prompt,
  RAGAS/Haiku): **paraphrase context_recall +0.11 (0.78 → 0.89)** — the target cell, at chunk
  level (doc-level source-hit was already 100% on both arms); paraphrase faithfulness +0.05;
  overall recall +0.029, faithfulness +0.023; factual (n=42) flat; abstention 77/81 on both
  arms — no OOS-moat erosion.
- Latency: end-to-end median 8.4s → 14.4s, but decomposition showed most of the delta is
  unified-memory contention (same MiniLM rerank +3.4s, same generator +0.8s) from a third
  resident model, not embedder cost; intrinsic query-embed ≈ +0.9s. Accepted.
- `max_distance` recalibration for the new distance distribution: **negative, kept 0.5**. A/B
  0.5 vs 0.6 was a dead heat (61/81 rows bit-identical); the knob only pre-filters the dense
  arm and rerank+margin re-select the context, absorbing pool changes. max_distance is not
  the OOS moat (closest OOS distances overlap in-scope); the generator and reranker are.
- **Standing baseline re-locked 2026-07-11** on the expanded 131-row non-holdout dataset
  (81 regression + 50 dev), tag `gemma4-e4b_qwen06-baseline-131_20260711_104509`, git
  bdf2b38: faithfulness .900 / answer_relevancy .770 / context_precision .687 /
  context_recall .833; abstention 123/131. Regression-split aggregates match the 7/10 run
  within noise; 80 shared rows had byte-identical contexts except one (eval_056, explained by
  the BP 195 §9 splice removing a stale lexical-bait chunk).

## Alternatives Considered

1. Qwen3-Embedding-4B — skipped: 0.6B already banked the paraphrase win; a ~2.5GB embedder
   beside the 9.6GB generator would deepen the memory contention that produced the +6s
   artifact, for marginal MTEB headroom. Revisit only if the generator shrinks or the corpus
   outgrows 0.6B.
2. Keep nomic and attack paraphrase recall with HyDE — deferred, not rejected: HyDE is the
   sibling lever on the same gap (no new resident model) and remains queued for isolated A/B.
3. Bedrock Titan for local too — rejected: breaks local-first; Titan is already the
   cloud-profile parity backend.

## Consequences

- Local dense retrieval is instruction-conditioned: retrieval experiments must hold
  `embedding_query_instruction` fixed or treat it as a variable under test.
- Two Qdrant collections coexist (`ph_law` 768 legacy, `ph_law_qwen06` 1024 active); reindex
  targets the active one. Dimension mismatches fail loudly via `Settings` validation.
- Eval baselines before 2026-07-10 measured the nomic stack; cross-era comparisons must use
  the re-locked 131-row baseline above as the reference point.
- The RAGAS judge still embeds with nomic — judge scores are comparable across the swap.
