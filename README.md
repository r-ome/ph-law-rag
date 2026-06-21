# ph-law-rag

A local-first RAG (Retrieval-Augmented Generation) assistant over Philippine law primary sources.

Built as a portfolio project demonstrating production-grade retrieval pipeline design: hybrid dense + sparse search, cross-encoder reranking, incremental document sync, and grounded generation with source citations — all running locally with no cloud API dependencies (evals aside).

---

## What it does

1. Fetches a curated allowlist of Philippine law sources (4 civil-law statutes in V1)
2. Normalizes and hashes content for incremental sync — re-runs skip unchanged documents
3. Chunks, embeds, and stores vectors locally in Qdrant + a persisted BM25 index
4. Answers legal questions with a local LLM, grounded in retrieved context
5. Cites source documents and article/section numbers
6. Abstains when the corpus doesn't support a grounded answer
7. Scores answer quality via RAGAS semantic eval metrics
8. Exposes a Streamlit chat UI and a FastAPI for programmatic access

---

## Stack

| Concern | Tool |
|---|---|
| RAG orchestration | LlamaIndex |
| LLM | Ollama (Mistral) |
| Embeddings | Ollama `nomic-embed-text` (768-dim) |
| Vector store | Qdrant (local Docker) |
| Sparse index | LlamaIndex BM25Retriever |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| PDF ingestion | `pdfplumber` |
| HTML ingestion | `trafilatura` + BeautifulSoup fallback |
| Evals | RAGAS (Anthropic judge) |
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
- [Ollama](https://ollama.ai/) installed and running natively on the host

> Ollama runs on the **host**, not in a container — on Apple Silicon only the host can use the GPU. See [`docs/local_setup.md`](docs/local_setup.md) for the full rationale.

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/your-username/ph-law-rag.git
cd ph-law-rag
uv sync

# 2. Start Qdrant (see "Run Qdrant locally" below if Docker isn't running yet)
docker compose up -d qdrant

# 3. Start Ollama bound to all interfaces, and pull the models
OLLAMA_HOST=0.0.0.0:11434 ollama serve   # in its own terminal
ollama pull mistral
ollama pull nomic-embed-text

# 4. Configure
cp .env.example .env

# 5. Initialize the DB and data dirs
raglab init

# 6. Populate the corpus — REQUIRED before asking anything (the DB ships empty)
raglab sync

# 7. Ask a question
raglab ask "What are the requisites of a valid contract under the Civil Code?"

# 8. Launch the UI
streamlit run app/ui/home.py

# 9. Run evals (requires anthropic_api_key in .env)
raglab eval
```

> **You must run `raglab sync` before querying.** The database ships empty; without a sync every query retrieves nothing and the system abstains.

For a step-by-step walkthrough and troubleshooting, see [`docs/local_setup.md`](docs/local_setup.md).

---

## Run the full stack with docker-compose

Runs Qdrant + FastAPI + Streamlit as three containers. Ollama stays on the host.

```bash
docker compose up        # Qdrant (:6333), API (:8000), UI (:8501)
```

Notes:
- Start host Ollama with `OLLAMA_HOST=0.0.0.0:11434 ollama serve` so the containers can reach it (the default `127.0.0.1` binding rejects container traffic).
- The cross-encoder reranker is **pre-baked into the image at build time**, so the first query isn't blocked on a slow model download.
- The `data/` volume is shared from the host — run `raglab sync` (locally or `docker compose exec api raglab sync`) before querying.

---

## Run Qdrant locally (without compose)

If you prefer to run Qdrant standalone, start your Docker runtime first:

- Docker Desktop: open the app and wait for it to report Docker is running
- Colima: `colima start` (first time: `colima start --cpu 4 --memory 8 --disk 20`)

```bash
docker ps              # confirm Docker is reachable
mkdir -p data/qdrant
docker run -d \
  --name ph-law-rag-qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v "$(pwd)/data/qdrant:/qdrant/storage" \
  qdrant/qdrant

curl http://localhost:6333/collections   # verify
```

The Qdrant dashboard is at `http://localhost:6333/dashboard`.

---

## CLI reference

| Command | Description |
|---|---|
| `raglab init` | Create data directories and bootstrap the SQLite database |
| `raglab sync` | Fetch, normalize, hash, version, chunk, embed, and index all enabled sources |
| `raglab ask "..."` | Ask a question and get a grounded answer with citations |
| `raglab retrieve "..."` | Inspect the retrieval trace for a query (no generation) |
| `raglab eval` | Run the eval pipeline over `data/eval_dataset.jsonl` |
| `raglab eval-score <run_path>` | Score an existing eval run with RAGAS |
| `raglab show-config` | Print the current config as JSON |
| `raglab healthcheck` | Verify Qdrant and Ollama are reachable |

---

## API

The FastAPI app exposes:

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check; verifies Qdrant and Ollama reachability |
| POST | `/query/ask` | Ask a question; returns answer + citations |
| GET | `/documents` | List all indexed documents |

Start the API:

```bash
uvicorn app.api.main:app --reload
```

---

## Corpus

V1 is deliberately scoped to **4 civil-law primary sources**:

- **Civil Code of the Philippines** (RA 386)
- **Electronic Commerce Act of 2000** (RA 8792)
- **Realty Installment Buyer Protection Act / Maceda Law** (RA 6552)
- **Consumer Act of the Philippines** (RA 7394)

The scope is intentional: a tight, coherent corpus keeps evals meaningful and makes abstention behavior testable — out-of-scope questions (e.g. criminal or constitutional law) *should* be refused, not guessed. The pipeline itself is source-agnostic; sources are defined in `sources/ph_law_sources.yaml` and toggled with `enabled`.

---

## Retrieval design

Hybrid retrieval is used because legal text needs both semantic understanding and exact-match precision:

| Query type | Dense | BM25 |
|---|---|---|
| "What makes a contract void?" | handles | — |
| "RA 8792 section 33" | — | handles |
| "Maceda Law grace period" | partial | handles |
| "rights of an installment buyer" | handles | partial |

Pipeline:
1. Embed query via `nomic-embed-text`
2. Dense retrieval from Qdrant (top-10)
3. BM25 sparse retrieval (top-10)
4. RRF fusion (k=60)
5. Cross-encoder reranking (top-5 pass to the context builder)
6. Abstention gate: if fewer than `min_chunks_for_answer` chunks survive, skip generation and return the abstain message — no LLM call
7. Grounded generation via Ollama, with `[n]` citations

See [`docs/architecture.md`](docs/architecture.md) for diagrams and [`docs/ADR/`](docs/ADR/) for the decisions behind these choices.

---

## Configuration

All config lives in `.env` and is loaded via `app/config.py`. Key settings:

```env
llm_model=mistral
embedding_backend=ollama
# embedding_model and embedding_dim are derived from the backend unless
# explicitly overridden together.
ollama_base_url=http://localhost:11434
qdrant_url=http://localhost:6333
chunk_size=256
chunk_overlap=32
dense_top_k=10
rerank_top_n=5
min_chunks_for_answer=2
debug=false
```

See `.env.example` for all options. When running under docker-compose, `qdrant_url` and `ollama_base_url` are overridden on the `api` service (`http://qdrant:6333` and `http://host.docker.internal:11434`).

---

## Project structure

```
ph-law-rag/
├── sources/
│   └── ph_law_sources.yaml     # curated source allowlist
├── app/
│   ├── config.py               # pydantic-settings config
│   ├── db.py                   # SQLite bootstrap + migrations
│   ├── cli/                    # Typer CLI (raglab)
│   ├── ingestion/              # fetch → parse → normalize → hash → version
│   ├── indexing/               # chunk → embed → upsert (Qdrant + BM25)
│   ├── retriever/              # dense + sparse → RRF → rerank → prompt → answer
│   ├── evals/                  # RAGAS scoring + report
│   ├── api/                    # FastAPI adapter
│   └── ui/                     # Streamlit adapter (home.py)
├── data/
│   ├── eval_dataset.jsonl      # eval questions (tracked)
│   ├── raw/                    # downloaded HTML/PDF (gitignored)
│   ├── normalized/             # cleaned text (gitignored)
│   ├── qdrant/                 # vector store (gitignored)
│   ├── bm25/                   # BM25 index (gitignored)
│   ├── sqlite/                 # raglab.db (gitignored)
│   └── eval_results/           # RAGAS outputs (gitignored)
├── tests/
│   ├── unit/                   # normalizer, hashing, chunker, abstention, RRF
│   └── integration/            # sync incremental, ask pipeline (gated)
└── docs/
    ├── project_plan.md         # source of truth for architecture
    ├── architecture.md         # system + pipeline diagrams
    ├── local_setup.md          # detailed setup + troubleshooting
    └── ADR/                    # architecture decision records
```

---

## Testing

```bash
uv run pytest -m "not integration"   # fast, deterministic — no services needed
uv run pytest                        # includes integration tests (need Qdrant + Ollama + a synced corpus)
```

Integration tests `skipif` cleanly when services are unavailable.

---

## Roadmap

| Milestone | Status |
|---|---|
| 1 — Scaffold and local runtime | ✅ complete |
| 2 — Document sync and normalization | ✅ complete |
| 3 — Chunking, embeddings, indexing | ✅ complete |
| 4 — Hybrid retrieval and generation | ✅ complete |
| 5 — Streamlit UI and FastAPI wiring | ✅ complete |
| 6 — Evals (RAGAS) | ✅ complete |
| 7 — Polish, docs, tests, docker-compose | ✅ complete |
| 8 — Conversation context (multi-turn, query rewriting) | ⬜ planned |

Future: cloud LLM backend (AWS Bedrock) with an eval delta vs. local, metadata filtering in Qdrant, expanded corpus, scanned-PDF OCR.

---

## Design decisions

Full rationale lives in [`docs/ADR/`](docs/ADR/). In brief:

**LlamaIndex over LangChain** — better-abstracted primitives for document retrieval; LangChain fits complex multi-step agent loops, which this isn't.

**Qdrant over ChromaDB** — native hybrid search and a stable concurrent-safe architecture. Tradeoff: requires Docker.

**Hybrid (dense + BM25) over dense-only** — legal text is full of exact citations (RA numbers, sections) that dense retrieval misses; BM25 covers them.

**RAGAS over custom scoring** — semantic, LLM-graded metrics beat keyword matching. Tradeoff: needs an LLM at eval time (Anthropic judge).

**Local-first with Ollama** — no cloud keys for the core pipeline; lower model quality than frontier APIs, but the backend is swappable via config.

**Abstention by design** — a confident wrong answer is worse than "I don't know" in a legal tool, enforced by a hard gate plus prompt instruction.

---

## License

MIT
