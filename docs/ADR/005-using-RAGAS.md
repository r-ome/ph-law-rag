# ADR-005: Using RAGAS framework over keyword-match eval

## Date

2026-06-11

## Status

Accepted

## Context

Evals are important for the quality of both the retriever and the generator. Evaluating metrics using a model rather than exact keyword would give a much more reliable metrics especially for answer relevancy, faithfulness and contextual relevancy.

## Decision

Use RAGAS (LLM-as-judge) eval framework.

## Alternatives Considered

1. Keyword-matching eval is considered. Rejected because it will not reflect accurate contextual metrics.
2. TruLens — LLM-as-judge eval with feedback functions and tracing/observability. Rejected because RAGAS has a tighter, RAG-specific metric set (faithfulness, answer/context relevancy) with less setup for this project's needs.
3. DeepEval — pytest-style LLM eval framework with a broad metric suite. Rejected because RAGAS is more focused on RAG metrics and is the established baseline for this project.

## Reasons

- RAGAS is reliable and ships a wrapper for models to judge the generative answer (llm-as-judge)

## Consequences

- LLM-as-judge costs tokens and is non-deterministic, its scores vary slightly between runs, and the judge model itself becomes part of the baseline.
- Dependency constraints: RAGAS 0.4.3 pins to the LangChain 0.3 line, so LangChain can't be upgraded freely without breaking evals. (PS: RAGAS sits on top of LangChain)
- Eval quality depends on a good golden/reference set, bad ground truth gives misleading metrics.
