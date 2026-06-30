# Overview

- A RAG Pipeline with the Philippine law as its corpus. A personal project that I want to work and experiment on. Currently, it runs using Streamlit as the UI and FastAPI as the API. You can ask questions related to the Philippine law and the model will answer your question and cite the sources, articles, etc where it's located in the Philippine Law. 23 Philippine-law primary sources are indexed (the allowlist in `sources/ph_law_sources.yaml`).

# System Diagram

```mermaid
flowchart LR
%%{init: {'theme':'dark'}}%%
User([User]) --> UI[Streamlit UI<br/>home.py]
UI -->|HTTP| API[FastAPI<br/>routes_query.py]
API -->|create or continue session| Conv[(SQLite<br/>conversations<br/>conversation_turns)]
API --> AS[answer_service]
AS -->|load recent turns| Conv
AS --> Rewriter[query_rewriter]
Rewriter -->|standalone query| AS

Dense --> Qdrant[(Qdrant<br/>vectors)]
Sparse --> BM25[(BM25 index)]
Dense -.embeds query.-> Ollama[Ollama<br/>nomic-embed-text]
Rewriter -.rewrite with LLM.-> LLM[Ollama or cloud LLM]

AS --> HR[hybrid_retriever<br/>RRF fusion]
HR --> Dense[dense_retriever] --> HR
HR --> Sparse[sparse_retriever] --> HR
HR --> Reranker
Reranker --> Gen["generator(Mistral model)"]
AS --> Abstain{is_abstention?}
AS -->|persist turn| Conv

subgraph Sync["'raglab sync'" command — offline]
    Fetch[fetcher] --> Parse[parser] --> Norm[normalizer] --> Index[index_service]
    Index --> Qdrant
    Index --> BM25
end
```

## Sync Pipeline

```mermaid
flowchart LR
%%{init: {'theme': "dark"}}%%
    f[Fetch]
    p[Parse]
    n[Normalize]
    h[Hash]
    c[Chunk]
    v[Version]
    i[Index]

f --> p;
p --> n;
n --> h;
h -.compare to latest hash.-> Match{if match}
Match -.false.-> v
Match -.true.-> skip
v --> c;
c --> i;
```

## Indexing Pipeline

```mermaid
flowchart LR
%%{init: {'theme':'dark'}}%%
chunk --> Embed["embed(nomic-embed-text)"] --> delete-stale --> store["store in Qdrant + BM25"];
```

## Query Pipeline

```mermaid
flowchart LR
%%{init: {'theme':'dark'}}%%
question["user question + optional session_id"] --> history["load recent conversation turns"]
history --> rewrite["rewrite follow-up<br/>to standalone query"]
rewrite --> retrieve["dense + sparse<br/>(Qdrant) (BM25)"]
retrieve --> merge["reciprocal rank fusion(RRF)"]
merge --> rerank["rerank<br/>(ms-marco-MiniLM-L-6-v2)"]
rerank--> gate
gate --> abstain{abstain?}
abstain -.answer.-> generate["Generate (Mistral)"]
abstain -.abstain.-> generate
generate --> persist["persist conversation_turn<br/>question, rewritten_question,<br/>answer, retrieved chunks"]
```

## Eval Pipeline

```mermaid
flowchart LR
%%{init: {'theme':'dark'}}%%
query[User's Query] --> answer["Query pipeline<br/>(refer to Query Pipeline)"]
answer --> ragas["RAGAS<br/>(claude-sonnet-4-6)"]
ragas --> report;
```

# Data Flow

1. User asks a question in a Streamlit chat session.
2. The API creates or continues a `session_id`, then `answer_service` loads recent turns from SQLite.
3. Follow-up questions are rewritten into a standalone query before retrieval.
4. The rewritten query passes through BM25 retrieval (sparse) and Qdrant retrieval (dense cosine similarity).
5. Chunks retrieved are merged using RRF (Reciprocal Rank Fusion), then reranked using the cross-encoder model.
6. Top chunks are passed to the generator model with the rewritten query.
7. The answer, original question, rewritten question, and retrieved chunk IDs are persisted to `conversation_turns`.

# Storage

- Qdrant as Vector Store for embeddings
- sqlite3 for documents, chunks, versions, metadata, sync_runs, conversations, conversation_turns
- raw files get saved (html, normalized and hashed) on data/
