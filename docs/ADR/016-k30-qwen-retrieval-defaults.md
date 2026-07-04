# ADR-016: Trace-first retrieval budget — dense_top_k=30 + Qwen3 reranker as defaults

## Date

2026-07-03

## Status

Accepted

## Plain English

The five hardest retrieval misses weren't missing from the index — they were sitting at dense ranks 11–29, just below the cutoff. We widened the candidate pool to 30 and swapped the MiniLM cross-encoder for Qwen3-Reranker-0.6B to sort the bigger pool correctly. This is an eval/offline default; it is too slow for serving.

## Context

`context_precision` was flat and 5 eval rows were hard PROVISION_MISS (recall 0.00). The original plan was to build per-provision boosts and tune the reranker. A $0 retrieve-trace pass first showed all 5 targets were stage-2 losses: present in dense results at ranks 11–29, cut by `dense_top_k=10` (Art 1475 missed by one rank). BM25 contributed nothing (no stemming, paraphrase gap). Raising top_k alone fixed 2/5 outright but exposed a true MiniLM ranking failure on Art 3 and Art 1475, where the definition chunk ranked below worked examples.

## Decision

- `dense_top_k` 10 → 30, `rerank_top_n=8`, `rerank_score_margin=6.0` retained.
- `reranker_backend=qwen3` (`Qwen/Qwen3-Reranker-0.6B`) as the default rerank stage; MiniLM kept as the alternate backend.
- Qwen must call `torch.mps.empty_cache()` per scoring call — without it the MPS allocator hoards ~17GB, swaps, and silently corrupts fp16 scores while exiting 0.

## Alternatives Considered

1. Per-provision boost table — the A/B arm had answer-key leakage (boosts derived from eval targets); rejected as unsound.
2. Single top_k sweep, keep MiniLM — no single k wins; §6 and Art 100 remain edge-track failures at any k.
3. BM25 tuning — dead end; sparse retrieval can't bridge the paraphrase gap without stemming.

## Reasons

- Trace evidence, not intuition: 5/5 failures were budget cutoffs, not embedding gaps.
- Judged eval graduated the change: felony/Art 1475/§11 recall 0 → 1.00, precision +0.106, aggregates faith .789→.815 / recall .857→.881, zero preflight regressions.
- Qwen3 beat both MiniLM-at-k30 and the boost arm head-to-head (Art 3 and Art 1475 to rank 1).

## Consequences

- Qwen3 costs ~10s/query on MPS (CPU unusable) — acceptable for eval, not serving. Serving needs a cascade (MiniLM first pass → Qwen on close calls) or MiniLM+deterministic signals; that decision is open.
- Full eval run is now ~30 min (21.5s/row).
- §6 remains unreachable by budget/reranker changes (edge-candidate track).
