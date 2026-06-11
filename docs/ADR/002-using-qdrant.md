# ADR-002: Using Qdrant

## Date

2026-06-11

## Status

Accepted

## Context

A RAG project needs vector store to store embeddings. Chroma and Qdrant offers the same features. Chroma is better for lightweight in-process vector store and better for prototyping RAG projects with zero infrastructure needed to maintain. Qdrant is build to run as a service, needs a bit more setup for local-only prototype but stronger in production story, rich in payload filtering, hybrid (dense + sparse) search natively, and scales horizontally.

## Decision

Use Qdrant, since it scales better for this project.

## Alternatives Considered

1. Chroma is better for prototyping and has zero maintenance required. Rejected because of planning to scale this project.
2. FAISS (Facebook AI Similarity Search) offers fast vector-search library, but no metadata filtering, persistence or server. Rejected because I'd have to build the database parts around it myself.

## Reasons

- Qdrant is better on the long run since I'm scaling this project to a larger corpus.

## Consequences

- A lot is required for setting up the project and need to consider infrastructures to maintain.
