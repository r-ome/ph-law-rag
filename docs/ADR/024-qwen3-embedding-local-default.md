# ADR-024: Qwen3-Embedding 0.6B as the local retrieval default

## Date

2026-07-11

## Status

Accepted

## Plain English

The local search index now uses Qwen3-Embedding 0.6B because it finds the right
legal passage more reliably when a question uses ordinary language instead of
the statute's wording. It improved the intended paraphrase slice without
weakening the out-of-scope fence. The older Nomic index remains available as the
rollback path, and cloud deployment continues to use Bedrock Titan embeddings.

## Context

The original local index used `nomic-embed-text` at 768 dimensions. Document-level
source recall was already strong, but lay-to-legal paraphrases still missed the
right supporting chunks. Qwen3-Embedding supports asymmetric retrieval: source
documents are embedded as written, while queries receive a short legal-retrieval
instruction. That mechanism directly targets the observed vocabulary gap.

Changing an embedding model changes retrieval truth, not merely infrastructure.
It requires a collection with the matching vector dimension, a complete reindex,
and a new locked eval baseline. Reusing the old collection would either fail its
dimension check or, worse, make results incomparable with the Nomic baseline.

## Decision

- The `ollama` embedding defaults are `qwen3-embedding:0.6b` and 1024 dimensions.
- The local Qdrant collection is `ph_law_qwen06`; the legacy 768-dimensional
  `ph_law` collection is retained for rollback and historical comparisons.
- Query embeddings are prefixed with: `Given a Philippine law question, retrieve
  the statutory provisions and jurisprudence that answer it.` Document chunks
  are embedded without that query instruction.
- `embedding_backend` remains the source of truth. `Settings` derives and
  validates the model and dimension unless an intentional custom override is
  supplied.
- `max_distance` remains `0.5`. A matched `0.5` versus `0.6` experiment showed
  that the downstream reranker makes this coarse dense-candidate cutoff nearly
  inert; loosening it did not improve the target slices.
- RAGAS continues to use `nomic-embed-text` as its judge-side embedding. Keeping
  the evaluation instrument fixed prevents the retrieval intervention from also
  changing how answer relevancy is scored.
- Cloud deployment remains on Bedrock Titan Text Embeddings v2. This ADR changes
  the local-first development and evaluation stack, not the cloud runtime
  profile in ADR-020.

## Evidence

The graduation A/B held the generator (`gemma4:e4b`), MiniLM reranker, retrieval
policy, corpus, and Haiku RAGAS judge fixed. Only the indexed embedding model and
the Qwen query instruction changed:

| Metric | Nomic | Qwen3 0.6B | Delta |
|---|---:|---:|---:|
| Faithfulness | 0.878 | 0.901 | +0.023 |
| Context recall | 0.809 | 0.838 | +0.029 |
| Context precision | 0.677 | 0.681 | +0.004 |
| Answer relevancy | 0.774 | 0.769 | -0.005 |
| Correct abstention | 77/81 | 77/81 | 0 |
| Paraphrase context recall (n=9) | 0.78 | 0.89 | +0.11 |
| Paraphrase faithfulness (n=9) | 0.88 | 0.93 | +0.05 |

The gain landed on the pre-declared target slice, factual performance was flat,
and the out-of-scope moat did not regress. A follow-up distance analysis found
every in-scope first-source hit within distance 0.427; the `0.5` versus `0.6` A/B
was effectively flat because MiniLM reranking discarded the extra marginal
candidates.

The standing post-graduation baseline is recorded separately below so future
experiments can distinguish the 81-row graduation A/B from the expanded corpus
scoreboard.

### Locked baseline (131 non-holdout rows)

Run `gemma4-e4b_qwen06-baseline-131_20260711_104509` re-locked the expanded
routine scoreboard after the corpus and eval-set growth. Configuration: `local`
profile, `gemma4:e4b` generator, Qwen3 0.6B embeddings, `ph_law_qwen06`, MiniLM
reranker, router off, min-chunks evidence gate, CRAG off; splits `regression` and
`dev`. The 30-row write-once holdout was deliberately excluded.

| Metric | Locked baseline |
|---|---:|
| Rows | 131 |
| Scored answers | 113 |
| Correct abstention decisions | 123/131 (93.9%) |
| Faithfulness | 0.8998 |
| Answer relevancy | 0.7701 |
| Context precision | 0.6868 |
| Context recall | 0.8330 |

Generation completed with zero pipeline errors. Median end-to-end latency was
13.31 seconds per row; MiniLM reranking had a 731 ms median. RAGAS used the
Anthropic Haiku judge and the standing row cache: 65 cache hits and 48 newly
costed rows. This artifact is the comparison point for future routine A/Bs on the
expanded dataset; the earlier 81-row Nomic/Qwen pair remains the causal evidence
for the embedding-model decision itself.

## Alternatives Considered

1. Keep Nomic as the local default — rejected after Qwen produced a targeted
   paraphrase-recall gain with no abstention regression. Nomic remains the
   explicit rollback arm.
2. Adopt Qwen3-Embedding 4B immediately — deferred. It requires another full
   reindex and matched evaluation; the 0.6B model already cleared the graduation
   bar with a much smaller local footprint.
3. Add HyDE globally — deferred. HyDE changes the query through generation and
   adds latency and failure modes. It must earn a lane-specific matched A/B on
   the paraphrase or future advice lane rather than ride on Qwen's result.
4. Retune `max_distance` as part of graduation — tested and declined. The knob
   was functionally inert after hybrid fusion and reranking.
5. Replace the judge embedding with Qwen too — rejected for this decision. That
   would change both the system under test and the measuring instrument.

## Consequences

- A local reindex must use the 1024-dimensional `ph_law_qwen06` collection.
- Query embedding has a small intrinsic latency cost, and running the embedder,
  Gemma4, and MiniLM together can create larger unified-memory contention on
  Apple Silicon. The accepted trade is improved paraphrase retrieval.
- Rollback is explicit: select `nomic-embed-text`, dimension 768, collection
  `ph_law`, and a blank query instruction, then use the matching legacy index.
- Future embedding or query-transformation experiments compare against the
  locked Qwen baseline and report target-slice, non-target, abstention, latency,
  and changed-context results separately.
