# ADR-008: Abstention by design

## Date

2026-06-11

## Status

Accepted

## Plain English

When the system does not have enough evidence, it should say so instead of guessing. For legal questions, a cautious refusal is better than a confident but unsupported answer.

## Context

This is a legal tool: a confident wrong answer can misguide a real legal decision, which is worse than "I don't know". The system must refuse when the corpus doesn't support a grounded answer.

## Decision

Abstain in two layers:

1. **Hard gate** — fewer than `min_chunks_for_answer` chunks after retrieval + reranking returns a fixed `ABSTAIN_MESSAGE`, no LLM call.
2. **Prompt + detection** — the prompt restricts answers to the given context; `is_abstention()` catches an abstaining answer and drops sources.

## Alternatives Considered

1. Always answer (no gate). Rejected — invites hallucinated provisions.
2. Prompt-only (no gate). Rejected — the LLM alone is unreliable; a deterministic gate guarantees refusal on thin context.

## Reasons

- A wrong answer is more harmful than an abstention here.
- Deterministic gate for thin context; prompt layer for thin-but-nonzero context.
- Pairs with the trimmed corpus — out-of-scope questions should abstain, not guess.

## Consequences

- False abstentions when retrieval underperforms — accepted trade-off, precision over recall.
- `min_chunks_for_answer` tunes abstention rate vs. coverage.
- Abstention is a tracked eval metric; `is_abstention()` must stay reliable across prompt/model changes.
