# ADR-003: Implementing hybrid retrieval

## Date

2026-06-11

## Status

Accepted

## Context

The corpus is Philippine Laws, retrieval is crucial for this project. Sparse retrieval would have exact text citations and Dense retrieval would retrieve relevant information based on meaning. Dense Retrieval would not be enough to fetch the relevant chunks for a user's query. Hybrid Retrieval combines both techniques which would make the retrieval more precise and accurate.

## Decision

Use hybrid retrieval for retrieving not only relevant chunks but also exact keywords.

## Alternatives Considered

1. Dense Retrieval ONLY fetches the contextual embeddings for the user's query. Rejected because users might need exact keyword match for this type of corpus.

2. Sparse Retrieval(BM25) ONLY. Rejected because keyword matching alone misses paraphrase/semantic queries. e.g. User asks "can my landlord evict me" but the law says "lessor"/"ejectment".

## Reasons

- Since the corpus is law, retrieval might need both the contextual(dense) and lexical(sparse) retrieval to make it more accurate and precise.

## Consequences

- Implementing Reciprocal Rank Fusion(RRF) for sorting both top_k of dense and sparse retrieval.
- Tweaking retrieval will be more tedious because both retrieval have different approach on retrieving chunks.
