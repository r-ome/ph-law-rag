# ph-law-rag

A local-first RAG (Retrieval-Augmented Generation) assistant over Philippine law primary sources — statutes, Supreme Court decisions, and the 1987 Constitution.

Built as a portfolio project demonstrating production-grade retrieval pipeline design: hybrid dense + sparse search, cross-encoder reranking, incremental document sync, and grounded generation with source citations — all running locally with no cloud API dependencies.

---

## What it does

1. Fetches a curated allowlist of Philippine law pages and PDFs (~48 sources)
2. Normalizes and hashes content for incremental sync — re-runs skip unchanged documents
3. Chunks, embeds, and stores vectors locally in Qdrant
4. Answers legal questions with a local LLM, grounded in retrieved context
5. Cites source documents and article/section numbers
6. Abstains when evidence is insufficient
7. Scores answer quality via RAGAS semantic eval metrics
8. Exposes a Streamlit chat UI and a FastAPI for programmatic access

---

## Stack

| Concern | Tool |
|---|---|
| RAG orchestration | LlamaIndex |
| LLM | Ollama (Mistral or Llama3) |
| Embeddings | Ollama `nomic-embed-text` (768-dim) |
| Vector store | Qdrant (local Docker) |
| Sparse index | LlamaIndex BM25Retriever |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| PDF ingestion | `pdfplumber` |
| HTML ingestion | `trafilatura` + BeautifulSoup fallback |
| Evals | RAGAS |
| Frontend | Streamlit |
| API | FastAPI |
| Config | pydantic-settings |
| Metadata / versioning | SQLite |
| Dependency management | uv |

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- [Docker](https://docs.docker.com/get-docker/) with a running local Docker daemon
- [Colima](https://github.com/abiosoft/colima) if you use the Docker CLI without Docker Desktop on macOS
- [Ollama](https://ollama.ai/) installed and running

---

## Run Docker Locally

This project expects Qdrant to run locally in Docker and persist data into `data/qdrant/`.

Start your local Docker runtime first:

- Docker Desktop: open Docker Desktop and wait for it to report that Docker is running
- Colima: run `colima start`

If this is your first Colima start, or the default profile is missing, use:

```bash
colima start --cpu 2 --memory 4 --disk 20
```

Check that Docker is available before starting Qdrant:

```bash
docker ps
docker context ls
```

If `docker` is pointed at the `colima` context, `colima` must be running. If you use Docker Desktop instead, switch contexts with:

```bash
docker context use default
```

Start Qdrant with local persistence:

```bash
mkdir -p data/qdrant
docker run -d \
  --name ph-law-rag-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant
```

Verify that Qdrant is up:

```bash
docker ps
curl http://localhost:6333/collections
```

Useful local commands:

```bash
docker stop ph-law-rag-qdrant
docker start ph-law-rag-qdrant
docker logs ph-law-rag-qdrant
```

The Qdrant dashboard is available at `http://localhost:6333/dashboard`.

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/your-username/ph-law-rag.git
cd ph-law-rag

# 2. Install dependencies
uv sync

# 3. Start Docker locally
# Docker Desktop: open the app
# Colima: colima start

# 4. Start Qdrant
mkdir -p data/qdrant
docker run -d \
  --name ph-law-rag-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant

# 5. Pull Ollama models
ollama pull mistral
ollama pull nomic-embed-text

# 6. Configure environment
cp .env.example .env
# Edit .env if needed — defaults work for a standard local setup

# 7. Initialize data directories and database
raglab init

# 8. Sync the corpus (fetch, normalize, hash, version documents)
raglab sync

# 9. Ask a question
raglab ask "What are the elements of a valid contract under the Civil Code?"

# 10. Launch the Streamlit UI
streamlit run app/ui/app.py

# 11. Run evals
raglab eval
```

---

## CLI reference

| Command | Description |
|---|---|
| `raglab init` | Create data directories and bootstrap the SQLite database |
| `raglab sync` | Fetch, normalize, hash, and version all enabled sources |
| `raglab ask "..."` | Ask a question and get a grounded answer with citations |
| `raglab ask --session <id> "..."` | Continue a named conversation session |
| `raglab eval` | Run RAGAS eval on `data/eval_dataset.jsonl` |
| `raglab show-config` | Print the current config as JSON |
| `raglab healthcheck` | Verify Qdrant and Ollama are reachable |

---

## API

The FastAPI app exposes:

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/query/ask` | Ask a question; returns answer + citations |
| GET | `/documents` | List all indexed documents |
| GET | `/documents/{doc_id}` | Single document metadata |
| POST | `/sync` | Trigger a sync as a background task |

Start the API:

```bash
uvicorn app.api.main:app --reload
```

---

## Corpus

~48 curated sources across:

- **Constitutional** — 1987 Constitution
- **Civil law** — Civil Code (RA 386), Family Code (EO 209)
- **Criminal law** — Revised Penal Code (Act 3815)
- **Labor law** — Labor Code (PD 442)
- **Special laws** — Anti-VAWC (RA 9262), Cybercrime Prevention Act (RA 10175), Data Privacy Act (RA 10173), IP Code (RA 8293), and more
- **Supreme Court decisions** — 20+ landmark decisions from the SC E-Library

Sources are defined in `sources/ph_law_sources.yaml`. Set `enabled: false` to exclude a source from sync.

---

## Retrieval design

Hybrid retrieval is used because Philippine law requires both semantic understanding and exact-match precision:

| Query type | Dense | BM25 |
|---|---|---|
| "What are the elements of estafa?" | handles | — |
| "Republic Act 10173 section 16" | — | handles |
| "G.R. No. 12345" | — | handles |
| "rights of an accused person" | handles | partial |

Pipeline:
1. Embed query via `nomic-embed-text`
2. Dense retrieval from Qdrant (top-10)
3. BM25 sparse retrieval (top-10)
4. RRF fusion (k=60)
5. Cross-encoder reranking (top-5 pass to context builder)
6. Abstention gate: if fewer than `min_chunks_for_answer` chunks survive the distance filter, skip generation
7. Grounded generation via Ollama

---

## Configuration

All config is in `.env` and loaded via `app/config.py`. Key settings:

```env
LLM_MODEL=mistral
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://localhost:11434
QDRANT_URL=http://localhost:6333
CHUNK_SIZE=256
CHUNK_OVERLAP=32
DENSE_TOP_K=10
RERANK_TOP_N=5
DEBUG=false
```

See `.env.example` for all options with descriptions.

---

## Project structure

```
ph-law-rag/
├── sources/
│   └── ph_law_sources.yaml     # curated source allowlist
├── app/
│   ├── config.py               # pydantic-settings config
│   ├── db.py                   # SQLite bootstrap + migrations
│   ├── ingestion/              # fetch → parse → normalize → version
│   ├── indexing/               # chunk → embed → upsert (Qdrant + BM25)
│   ├── retrieval/              # dense + sparse → RRF → rerank
│   ├── generation/             # prompt → Ollama → answer
│   ├── evals/                  # RAGAS scoring
│   ├── conversation/           # session management + query rewriting
│   ├── api/                    # FastAPI adapter
│   └── ui/                     # Streamlit adapter
├── data/
│   ├── eval_dataset.jsonl      # eval questions (tracked)
│   ├── raw/                    # downloaded HTML/PDF (gitignored)
│   ├── normalized/             # cleaned text (gitignored)
│   ├── qdrant/                 # vector store (gitignored)
│   ├── bm25/                   # BM25 index (gitignored)
│   ├── sqlite/                 # raglab.db (gitignored)
│   └── eval_results/           # RAGAS outputs (gitignored)
├── tests/
│   ├── unit/
│   └── integration/
└── docs/
    └── project_plan.md         # source of truth for architecture
```

---

## ROADMAP

### Milestone 1 — Scaffold and Local Runtime `✅ complete`

- Repo structure, `pyproject.toml`, `config.py`, `db.py`
- Typer CLI with `init`, `sync`, `ask`, `eval`, `healthcheck`, `show-config` stubs
- FastAPI `/health` route
- Streamlit stub

### Milestone 2 — Document Sync and Normalization `✅ complete`

- `sources/ph_law_sources.yaml` — 48 sources across constitutional, civil, criminal, labor, special laws, and SC decisions
- `fetcher.py` — httpx downloader with timeout and user-agent
- `parser.py` — PDF (pdfplumber) and HTML (trafilatura + BeautifulSoup fallback) extraction
- `normalizer.py` — whitespace collapse, dedup blank lines
- `hashing.py` — SHA-256 of normalized text
- `storage.py` — hash comparison, disk write, SQLite versioning
- `sync.py` — orchestrator with per-source status reporting and `sync_runs` tracking
- 48 documents fetched, normalized, and versioned

### Milestone 3 — Chunking, Embeddings, and Indexing `⬜ next`

- `chunker.py` — LlamaIndex SentenceSplitter (256 tokens, 32 overlap)
- `embedder.py` — Ollama `nomic-embed-text` client
- `vector_store.py` — Qdrant wrapper (upsert, delete-by-doc, query)
- `bm25_store.py` — BM25Retriever build, persist, and load
- `index_service.py` — delete stale → chunk → embed → upsert → rebuild BM25
- Wire indexing into `raglab sync`

### Milestone 4 — Hybrid Retrieval and Generation `⬜ planned`

- `dense_retriever.py`, `sparse_retriever.py`, `hybrid_retriever.py` (RRF fusion)
- `reranker.py` — cross-encoder rescoring
- `context_builder.py` — numbered context block with citations
- `prompts.py` — grounded system prompt with abstention instruction
- `llm_client.py` — Ollama HTTP client
- `answer_service.py` — full ask pipeline with debug trace
- Wire `raglab ask` end-to-end

### Milestone 5 — Streamlit UI and FastAPI Wiring `⬜ planned`

- Streamlit: chat UI with citations, source browser, settings sidebar, Sync Now button
- FastAPI: `/query/ask`, `/documents`, `/sync` routes
- Both adapters call shared service modules — no business logic in either

### Milestone 6 — Evals `⬜ planned`

- 40–60 question eval dataset across factual, paraphrase, synthesis, ambiguous, and out-of-scope categories
- `runner.py`, `ragas_scorer.py`, `report.py`
- RAGAS metrics: faithfulness, answer relevance, context precision, context recall
- `raglab eval` produces a category-level summary table

### Milestone 7 — Polish and GitHub Readiness `⬜ planned`

- `docs/architecture.md`, `docs/tradeoffs.md`, `docs/local_setup.md`
- Unit tests (normalizer, chunker, hash logic) and integration tests (sync, ask pipeline)
- `docker-compose.yml` for Qdrant
- Repo is clone-and-run ready in under 15 minutes

### Milestone 8 — Conversation Context Management `⬜ planned`

- `conversations` and `conversation_turns` SQLite tables
- `app/conversation/session.py` — create, load, append turns
- `app/conversation/query_rewriter.py` — resolve pronouns and ellipsis into standalone queries
- `--session` flag on `raglab ask`; session threading in FastAPI and Streamlit

---

## Design decisions

**LlamaIndex over LangChain** — LlamaIndex has more opinionated, better-abstracted primitives for document retrieval. For a RAG-first project it's the right choice. LangChain would be appropriate for complex multi-step agent loops.

**Qdrant over ChromaDB** — Native hybrid search (dense + sparse in one query), stable concurrent-safe architecture. The tradeoff is requiring Docker for local setup.

**RAGAS over custom scoring** — Semantic scoring via LLM-graded metrics is more meaningful than keyword matching. Tradeoff: RAGAS requires a working LLM at eval time.

**Trafilatura over BeautifulSoup** — Strips navigation boilerplate, which is the primary quality problem for Philippine law pages. Falls back to BeautifulSoup when trafilatura returns nothing.

**Local-first with Ollama** — No cloud API keys required. Lower model quality than GPT-4/Claude, but the LLM backend is swappable via config if cloud inference is desired later.

---

## License

MIT
