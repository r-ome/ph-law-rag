# ADR-021: Bedrock Rerank as eval/host default; MiniLM stays the serving reranker

## Date

2026-07-07

## Status

Accepted (supersedes the open question in the ADR-016 addendum)

## Plain English

We replaced the laptop-GPU reranker (Qwen3) with Amazon's rented one for offline evals: same
answer quality, scored in under a second instead of six, no more memory babysitting. But the
account may only call it twice per minute and the limit can't be raised, so the public demo
keeps the small fast local reranker. The eval-vs-serving gap is now a documented quota split
between two near-equal rerankers, not a quality split.

## Context

ADR-016's addendum accepted a validity gap: eval ran qwen3 (best retrieval, GPU-only,
~6.5s/query on MPS), serving pinned MiniLM (weakest, but the only backend viable in a
CPU container). The eval therefore measured a retrieval stack that serving never ran. The
project had already closed this pattern twice with managed backends (embeddings → Bedrock
Titan, generation → Anthropic Haiku); the reranker was the last local-only heavy component.

## Decision

- `reranker_backend` gains a third backend, `bedrock` (Bedrock Rerank API,
  `amazon.rerank-v1:0`, `bedrock-agent-runtime.rerank`, one call per query,
  `numberOfResults=len(candidates)`). Scores are uncalibrated relevance floats — ordering
  only; plain top-8 trim, `rerank_score_margin` does not apply.
- The rerank client points at `bedrock_rerank_region=us-west-2` (rerank models are not
  served in us-east-1); everything else stays on `aws_region`.
- **Config default flips `qwen3` → `bedrock`**: host evals and host CLI now run bedrock.
- **Serving pins stay `minilm`** (docker-compose, docker-compose.cloud, infra
  `API_ENVIRONMENT`): amazon.rerank-v1 is quota-capped at **2 requests/minute,
  non-adjustable** (verified via service-quotas). Calls are paced 31s apart in
  `_bedrock_scores`; a live user's follow-up inside a minute would stall ~30s.
- qwen3 is retired to a research arm (code path kept, no surface defaults to it). MiniLM
  remains the offline/serving backup.
- No Fargate IAM change: serving never calls Rerank. If a serving flip ever happens,
  `bedrock:Rerank` needs its own statement with `Resource: "*"` (the action advertises no
  model resource type).

## Evidence

- $0.01 preflight (`scripts/trace_bedrock_ab.py`, 9 probes through the shipped `rerank()`
  path vs the qwen_top8_no_margin reference arm): bedrock at-or-better on 9/9, six rows
  improved, and `article_335_rape` recovered at rank 8 where qwen3 missed it outright.
- Judged A/B (81 rows, single-var swap, frozen Haiku generator + k30 + index; ~$2.15
  total): on the 66 rows scored by both runs — faithfulness .929→.921 (noise), context
  precision .806→.829, context recall .915→.898 where the entire recall delta is one row
  (online-libel lost the RPC penalty chunk from the top-8). Paraphrase precision +.12.
  Ledger: OOS fence 11/12 vs 12/12, but the leak answered from genuinely in-corpus
  Constitution Art IX-C content; two new false-abstains vs the baseline's own two.
- Latency: ~0.8s/query unthrottled vs qwen3's ~6.5s (MPS). Eval runs lose the 21.5s/row
  qwen3 cadence and the MPS `empty_cache`/fp16-corruption risk class entirely
  (pacing makes wall-clock similar, ~31s/row, but unattended and CPU-idle).

## Alternatives Considered

1. Serve bedrock bare — rejected: 2 rpm non-adjustable means visible ~30s stalls on any
   quick follow-up.
2. Bedrock with MiniLM fallback-on-throttle — rejected: under load the fallback is what
   actually serves, so "eval == serving" would be false exactly when traffic exists;
   silent quality wobble is worse than a documented split.
3. Cohere Rerank 3.5 — not needed: amazon.rerank-v1 passed preflight 9/9; Cohere's quota
   (3/min) is no better.
4. Keep qwen3 as eval default — rejected: bedrock matches its quality without the GPU
   babysitting, and remote scoring removes a whole local-corruption failure class.

## Consequences

- Eval-vs-serving gap is narrowed, not closed: serving runs MiniLM, evals run bedrock
  (qwen3-class). The residual split is quota-bound, not quality-bound, and documented here.
- Host `raglab ask` now paces consecutive queries ~31s apart; set
  `reranker_backend=minilm` in `.env` for rapid interactive poking.
- Eval runs need AWS credentials (`ph-law-rag-dev` profile) and touch us-west-2.
- Determinism caveat: Bedrock scores are a remote API's output; the planned rerank score
  cache keyed `(query, candidate-set hash, model)` is the follow-up that restores
  reproducibility for the deterministic-eval method.
- Corpus-expansion evals judge the bedrock config only (judge the shipped eval config).
