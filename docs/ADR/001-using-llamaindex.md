# ADR-001: Using LlamaIndex

## Date

2026-06-11

## Status

Accepted

## Context

This project is for RAG/search a library with an integration of a LLM and external data sources. It will require chunking, embedding, dense + lexical retrieval features. Local-first is currently an option since local open weight models provide the same feature the closed weight models (not the same reasoning power).

## Decision

Use LlamaIndex framework for orchestration between LLM and corpus.

## Alternatives Considered

1. LangChain has a similar philosophy and wrappers for LLM but has a different focus. It primarily focuses on chaining steps (agents), multi-step workflows and branching logic.

2. Building the tools natively also is considered. Rejected because reinventing chunking/retrieval/store glue might be a wasted effort since a framework already ships it.

## Reasons

- LlamaIndex has a primary focus on RAG projects although LangChain can also do what LlamaIndex provides, LlamaIndex still has the best features for this type of RAG project. It has chunking(SentenceSplitter), embedding wrapper for a model(OllamaEmbedding) and a lexical retriever (BM25).

## Consequences

- This project might evolve into a big project, so agents, multi-step workflows might be needed in the future. Migrating from LlamaIndex to LangChain would be a tedious task, but LlamaIndex possibly has those features too.
- Framework abstraction can obscure behavior: when chunking or retrieval results look wrong, debugging means digging into LlamaIndex internals rather than reading my own code.
