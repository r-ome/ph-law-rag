# Philippine Law RAG — Project Plan

This is the project reference document for `ph-law-rag`.

- Update this file whenever implementation meaningfully changes.
- Use this plan as the default source of truth for architecture, scope, and priorities during implementation and review.
- If the code intentionally differs from this plan, document the reason in review notes or adjacent docs.

See also: `/Users/jeromeagapay/Documents/Personal/muming/03_Outputs/ph-law-rag-devlog.md`

---

## Goal

Build a serious local-first Python portfolio project that demonstrates:

- LlamaIndex as a RAG orchestration framework
- Hybrid retrieval (dense + sparse BM25 with RRF fusion)
- Cross-encoder reranking for answer quality
- PDF and HTML document ingestion
- Incremental sync with content hashing
- Local LLM generation via Ollama with pluggable backends
- Semantic eval scoring via RAGAS
- Interactive Streamlit frontend
- Good software engineering structure

This should feel like a credible production-grade retrieval system over a real legal corpus, not a tutorial demo.

---

## Product Concept

A local RAG assistant over a curated set of Philippine law primary sources — statutes, Supreme Court decisions, and the 1987 Constitution.

It should:

1. Fetch a curated allowlist of law pages and PDFs
2. Normalize and hash content for incremental sync
3. Only reprocess changed or new documents
4. Chunk, embed, and store vectors locally in Qdrant
5. Answer legal questions with a local LLM, grounded in retrieved context
6. Cite source documents and article/section numbers
7. Abstain when evidence is insufficient
8. Support semantic eval scoring via RAGAS
9. Expose a Streamlit UI for interactive querying

---

## Scope Constraints

- Python 3.11+ (widely supported, no cutting-edge version requirement)
- Local-first: runs on a normal developer machine with no cloud AI accounts required
- Curated allowlist of URLs and PDFs only — no crawler
- LlamaIndex as the primary orchestration framework
- Ollama as the default LLM and embedding backend
- Keep scope realistic: ~25–40 documents in V1

---

## Recommended Stack

| Concern               | Tool                                   | Reason                                                                                                    |
| --------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| RAG orchestration     | LlamaIndex                             | Purpose-built for document retrieval pipelines; first-class hybrid retrieval, reranking, and eval support |
| LLM                   | Ollama (mistral or llama3)             | Local, free, model-swappable via config                                                                   |
| Embeddings            | Ollama `nomic-embed-text`              | Local, high-quality 768-dim embeddings; swap via config                                                   |
| Vector store          | Qdrant (local Docker)                  | Native hybrid search (dense + sparse in one query), metadata filtering, concurrent-safe                   |
| Sparse index          | LlamaIndex BM25Retriever               | Exact-match keyword retrieval; pairs with dense for hybrid                                                |
| Reranker              | Qwen3 reranker by default; `cross-encoder/ms-marco-MiniLM-L-6-v2` fallback | Qwen3 is the eval-quality selector default; MiniLM remains a latency-sensitive serving fallback |
| PDF ingestion         | `pdfplumber` via LlamaIndex            | Better table and layout handling than PyPDF2                                                              |
| HTML ingestion        | `trafilatura`                          | Strips navigation boilerplate better than BeautifulSoup                                                   |
| Evals                 | RAGAS                                  | Semantic eval scoring: faithfulness, answer relevance, context precision, context recall                  |
| Frontend              | Streamlit                              | Fast, Python-native, enough for a portfolio demo UI                                                       |
| API                   | FastAPI                                | Same service modules as Streamlit; thin adapter                                                           |
| Config                | pydantic-settings                      | `.env`-driven config with type validation and defaults                                                    |
| Metadata / versioning | SQLite                                 | Zero-setup, ships with Python, sufficient for the workload                                                |
| Dependency management | uv                                     | Fast, lockfile-based                                                                                      |
| Testing               | pytest                                 | Standard                                                                                                  |

---

## Architecture

The system has 5 parts:

### 1. Source Sync Pipeline

- `app.sync_service` owns the `raglab sync` source loop, per-source transaction boundary, indexing handoff, unchanged-content metadata reconcile, status counts, and `sync_runs` insert.
- `app.ingestion` owns source-local ingestion only: fetch, parse, normalize, hash, artifact writes, `documents` upsert, and `document_versions` insert.
- Read allowed sources from `sources/ph_law_sources.yaml`
- Fetch documents (HTTP for HTML, download for PDF)
- Extract text via `trafilatura` (HTML) or `pdfplumber` (PDF)
- Normalize content (whitespace collapse, dedup blank lines)
- Compute SHA-256 hash of normalized text
- Compare against latest stored version in SQLite
- Mark each doc as new, changed, unchanged, or failed
- Persist raw file and normalized text to disk
- Write metadata to SQLite `documents` and `document_versions`

### 2. Indexing Pipeline

- Process only new or changed documents
- Chunk via LlamaIndex `SentenceSplitter` (target ~256 tokens, overlap 32)
- Generate embeddings via Ollama (`nomic-embed-text`)
- Upsert dense vectors to Qdrant collection
- Build/update BM25 index (LlamaIndex `BM25Retriever`, persisted to disk)
- Delete stale vectors for changed documents before re-indexing
- Write chunk metadata to SQLite `chunks` table

### 3. Query Pipeline

- Embed user query via Ollama
- Run dense retrieval from Qdrant (top-k = 30 candidates by default)
- Run BM25 sparse retrieval (top-k = 10 candidates)
- Merge results via Reciprocal Rank Fusion (RRF)
- Re-score merged candidates with the configured reranker (`qwen3` by default; `minilm` fallback)
- Apply `max_distance` filter; apply `min_chunks_for_answer` gate
- Build numbered context prompt with source citations
- Generate answer via Ollama LLM
- Return answer + citation list

### 4. Eval Pipeline

- Load eval questions from `data/eval_dataset.jsonl`
- Run each question through the full ask pipeline
- Score results via RAGAS metrics: faithfulness, answer relevance, context precision, context recall
- Save eval artifacts under `data/eval_results/`: new runs use
  `runs/YYYY-MM-DD/<tag>/` with `run.jsonl`, `meta.json`, `summary.json`,
  and `scored.json`; legacy flat files remain readable through the artifact
  resolver
- Maintain `manifest.jsonl` and `latest.json` pointers for listing and
  comparing runs without opening every artifact bundle
- Print category-level report

### 5. Interface Layer

- **Streamlit app** — chat-style UI with sidebar for settings and source browser
- **FastAPI** — `/health`, `/query/ask`, `/documents`, `/sync` (for the API layer)
- Both call the same shared service modules; no business logic in either adapter. Runtime probes live in `app.runtime.health`, not in API adapters.

---

## Key Design Principles

### LlamaIndex as Orchestration, Not Lock-in

Use LlamaIndex abstractions for the retrieval and generation pipeline (`VectorStoreIndex`, `BM25Retriever`, `RetrieverQueryEngine`, `NodePostprocessor`, `ResponseSynthesizer`). Keep ingestion logic (fetching, hashing, versioning) outside LlamaIndex — it's just Python. This means swapping components (Qdrant → another vector store, Ollama → OpenAI) is a config change, not a rewrite.

### Hybrid Retrieval From Day One

Dense retrieval alone is insufficient for legal text. Philippine law is full of exact citations — Republic Act numbers, article references, G.R. numbers, section identifiers. BM25 handles these; dense handles semantic intent. Both are needed. RRF fusion is the merge strategy.

### Incremental Sync

Keep the hash-based incremental sync pattern from the original project. It's the right design. Hash normalized text (not raw), compare against SQLite, skip unchanged documents. This makes re-runs cheap and the system safe to run on a schedule.

### Grounded Generation with Abstention

The LLM must only answer from provided context. Abstention is enforced by two mechanisms:

1. Hard gate: if fewer than `min_chunks_for_answer` chunks survive the distance filter, skip generation and return an explicit "insufficient evidence" response.
2. Prompt instruction: the system prompt explicitly instructs the LLM to say it doesn't know when evidence is thin.

### PDF-First Corpus

Philippine law primary sources are predominantly PDFs. PDF ingestion is not optional. `pdfplumber` via LlamaIndex handles layout-aware extraction better than PyPDF2 for multi-column and table-heavy legal documents.

---

## Repository Structure

```
ph-law-rag/
├── README.md
├── pyproject.toml
├── .env.example
├── .python-version          # 3.11
├── sources/
│   └── ph_law_sources.yaml  # curated URL/PDF allowlist
├── app/
│   ├── config.py            # pydantic-settings config
│   ├── db.py                # SQLite bootstrap + migrations
│   ├── sync_service.py      # sync orchestration, transactions, indexing handoff
│   ├── source_metadata.py   # source → chunk metadata mapping
│   ├── runtime/
│   │   └── health.py        # Qdrant/Ollama HTTP probes shared by CLI/API/reindex
│   ├── ingestion/
│   │   ├── fetcher.py       # httpx downloader → FetchResult
│   │   ├── pdf_parser.py    # pdfplumber extraction
│   │   ├── html_parser.py   # trafilatura extraction
│   │   ├── normalizer.py    # whitespace cleanup
│   │   ├── storage.py       # hash compare, disk write, SQLite write
│   │   └── sync.py          # ingest one source; no indexing or sync_runs ownership
│   ├── indexing/
│   │   ├── chunker.py       # LlamaIndex SentenceSplitter wrapper
│   │   ├── embedder.py      # Ollama embedding client
│   │   ├── vector_store.py  # Qdrant wrapper (upsert, delete, query)
│   │   ├── bm25_store.py    # BM25Retriever build/persist/load
│   │   └── index_service.py # orchestrator: chunk → embed → upsert
│   ├── retrieval/
│   │   ├── dense_retriever.py    # Qdrant top-k dense retrieval
│   │   ├── sparse_retriever.py   # BM25 top-k retrieval
│   │   ├── hybrid_retriever.py   # RRF fusion of dense + sparse
│   │   ├── reranker.py           # cross-encoder rescoring
│   │   └── context_builder.py    # numbered prompt + source list
│   ├── generation/
│   │   ├── llm_client.py         # Ollama HTTP client
│   │   ├── prompts.py            # system + grounding prompt templates
│   │   └── answer_service.py     # full ask pipeline orchestrator
│   ├── evals/
│   │   ├── artifacts.py          # eval artifact paths, legacy fallback, manifest
│   │   ├── runner.py             # runs questions through ask pipeline
│   │   ├── ragas_scorer.py       # RAGAS metric computation
│   │   └── report.py             # aggregates + prints category report
│   ├── api/
│   │   └── main.py               # FastAPI routes
│   └── ui/
│       └── app.py                # Streamlit app
├── data/
│   ├── eval_dataset.jsonl        # tracked; eval questions + expected answers
│   ├── raw/                      # gitignored; downloaded HTML/PDF files
│   ├── normalized/               # gitignored; cleaned text
│   ├── qdrant/                   # gitignored; Qdrant local storage
│   ├── bm25/                     # gitignored; BM25 index files
│   ├── sqlite/                   # gitignored; raglab.db
│   └── eval_results/             # gitignored; eval run outputs
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── docs/
    ├── architecture.md
    ├── tradeoffs.md
    └── local_setup.md
```

---

## Milestones

### Milestone 1: Scaffold and Local Runtime

Goal: project boots cleanly, all entry points work.

Build:

- Repo structure and `pyproject.toml`
- `config.py` with pydantic-settings
- `db.py` with SQLite bootstrap and migration table
- Typer CLI stub with `init`, `sync`, `ask`, `eval`, `healthcheck`, `show-config` commands
- FastAPI app with `/health` route
- Streamlit stub with placeholder UI
- `raglab init` creates data directories and bootstraps DB

Definition of done:

- CLI runs without error
- FastAPI starts
- Streamlit starts
- Config loads from `.env`
- DB initializes cleanly

---

### Milestone 2: Document Sync and Normalization

Goal: sync fetches, normalizes, and versions documents.

Build:

- `sources/ph_law_sources.yaml` with ~25–40 curated sources
- `fetcher.py` — httpx downloader with timeout, user-agent, basic retry
- `pdf_parser.py` — pdfplumber extraction with fallback for scanned pages
- `html_parser.py` — trafilatura extraction with BeautifulSoup fallback
- `normalizer.py` — whitespace collapse, dedup blank lines
- Content hashing (SHA-256 of normalized text)
- `storage.py` — hash comparison, disk write, SQLite insert
- `sync_service.py` — orchestrator with per-source status reporting and `sync_runs`; `ingestion/sync.py` ingests one source
- SQLite `documents`, `document_versions`, `sync_runs` tables

Definition of done:

- `raglab sync` fetches all enabled sources
- Changed vs. unchanged is tracked and reported
- Re-running sync on unchanged corpus skips re-fetch versioning AND re-embedding

Metadata-only reconcile (added 2026-06-23): when content is unchanged but a manifest
field changed, the unchanged path still reconciles the derived stores instead of a blind
skip — because some manifest fields (notably `status`) are retrieval filters or are baked
into chunk text. Two tiers:

- Tier A (`status, url, tags, category, doc_type` — not baked): in-place Qdrant
  `set_payload` + chunk `metadata_json` merge + `chunk_parents.url` + BM25 rebuild, no
  re-embed. Reported `[META]`.
- Tier B (`title, official_number` baked into chunk text; `structure` changes boundaries;
  or zero existing chunks): re-chunk + re-embed under the existing `version_id`, no new
  `document_versions` row. Reported `[REINDEX]`.

Failures (e.g. Qdrant down) are counted `failed` with no commit — never a silent stale
skip. `sync_runs` has no columns for these; metadata-only reconciles are folded into
`unchanged_count` (granular `refreshed`/`reindexed_meta` counts live in the run return
dict). Known gap: disabling/removing a source leaves its indexed chunks orphaned
(`load_allowed_sources` skips disabled sources) — needs a separate all-source reconcile.

---

### Milestone 3: Chunking, Embeddings, and Indexing

Goal: changed documents are chunked, embedded, and stored in Qdrant and BM25.

Build:

- `chunker.py` — LlamaIndex `SentenceSplitter` with configurable size and overlap
- `embedder.py` — Ollama embedding client (`nomic-embed-text`)
- `vector_store.py` — Qdrant wrapper: collection init, upsert, delete-by-doc, dense query
- `bm25_store.py` — LlamaIndex `BM25Retriever` with build, persist, and load functions
- `index_service.py` — delete stale vectors, chunk → embed → upsert, update SQLite `chunks`
- Qdrant running locally via Docker

Definition of done:

- `raglab sync` triggers indexing for new/changed documents
- Qdrant holds dense vectors; BM25 index is persisted to disk
- Re-running on unchanged docs skips indexing entirely

---

### Milestone 4: Hybrid Retrieval and Generation

Goal: `raglab ask` returns grounded answers with citations.

Build:

- `dense_retriever.py` — Qdrant top-k dense retrieval with distance filter
- `sparse_retriever.py` — BM25 top-k retrieval
- `hybrid_retriever.py` — RRF fusion of dense and sparse result lists
- `reranker.py` — cross-encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`) with configurable top-n passthrough
- `context_builder.py` — numbered context block with source title, URL, and article/section metadata
- `prompts.py` — grounded system prompt; instructs LLM to cite by reference number and abstain when evidence is thin
- `llm_client.py` — Ollama HTTP client with structured error handling
- `answer_service.py` — full ask pipeline: retrieve → rerank → build context → check abstention gate → generate → package response
- Debug mode: exposes retrieved chunks, distances, rerank scores, prompt length

Definition of done:

- `raglab ask "..."` returns a grounded answer with numbered citations
- Out-of-scope questions trigger the abstention response
- Debug mode shows the full retrieval trace

---

### Milestone 5: Streamlit UI and FastAPI Wiring

Goal: interactive UI works; API is usable.

Build:

- `app/ui/app.py` — Streamlit chat interface:
  - Query input with submit
  - Answer display with inline citation links
  - Sidebar: model selector, top-k slider, debug toggle
  - Source browser tab: list indexed documents with sync status
- `app/api/main.py` — FastAPI routes:
  - `GET /health`
  - `POST /query/ask` — calls `answer_service`
  - `GET /documents` — lists all documents from SQLite
  - `POST /sync` — triggers sync (background task)
- Both Streamlit and FastAPI call the same shared service modules

Definition of done:

- Streamlit app runs and returns answers in a browser
- FastAPI `/query/ask` returns the same response programmatically
- No business logic lives in either adapter

---

### Milestone 6: Evals

Goal: eval pipeline produces meaningful semantic scores.

Build:

- `data/eval_dataset.jsonl` — 40–60 questions across:
  - Factual lookup (specific article, section, or RA number)
  - Paraphrase (same meaning, different wording)
  - Synthesis (requires combining multiple sources)
  - Ambiguous questions (may or may not be answerable from corpus)
  - Out-of-scope questions (should trigger abstention)
- `runner.py` — feeds questions through `answer_service`, saves results to JSONL
- `artifacts.py` — owns run tags, bundled artifact paths, legacy flat-file
  fallback, `manifest.jsonl`, and `latest.json`
- `ragas_scorer.py` — computes RAGAS metrics per question:
  - **Faithfulness** — is the answer grounded in the retrieved context?
  - **Answer relevance** — does the answer address the question?
  - **Context precision** — are the retrieved chunks actually relevant?
  - **Context recall** — does the retrieved context cover the expected answer?
- `report.py` — aggregates scores by question category, prints summary table
- CLI `raglab eval` runs the full cycle

Definition of done:

- `raglab eval` produces per-question RAGAS scores and a category summary
- Results are saved as bundled artifacts for manual review and indexed in
  `manifest.jsonl`

---

### Milestone 7: Polish and GitHub Readiness

Build:

- `README.md` with setup instructions, demo commands, example output
- `docs/architecture.md` — system design, data flow, package breakdown
- `docs/tradeoffs.md` — design decisions and reasoning
- `docs/local_setup.md` — detailed Qdrant Docker setup, Ollama model pull, first run
- Tests (unit for normalizer, chunker, hash logic; integration for sync and ask pipeline)
- `.env.example` with all configurable values documented
- `docker-compose.yml` for the full stack: **Qdrant + FastAPI + Streamlit** (3 services, one container each — not one container running all three). Ollama is **not** containerized.

#### Full-stack docker-compose design

Three services run in containers; Ollama runs natively on the host so it keeps Apple Silicon GPU access via Metal (containers run in a Linux VM with no Metal/GPU passthrough — a containerized Ollama would be CPU-only and slow). Qdrant/FastAPI/Streamlit are CPU+RAM only and do not use the GPU.

Services:

- `qdrant` — image `qdrant/qdrant`, ports `6333:6333` / `6334:6334`, volume for `/qdrant/storage` so vectors persist across restarts.
- `api` — FastAPI (uvicorn), port `8000:8000`, depends_on `qdrant`.
- `ui` — Streamlit, port `8501:8501`, depends_on `api`.

Networking rules (Compose puts services on a shared network where the **service name is the hostname**):

- `ui` → `api` at `http://api:8000` (not `localhost`).
- `api` → `qdrant` at `http://qdrant:6333` (not `localhost`).
- `api` → host-native Ollama at `http://host.docker.internal:11434`. Add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `api` service so this resolves on Colima and Docker Desktop alike.

Host prerequisites (document in README):

- Ollama installed natively, models pulled (`ollama pull nomic-embed-text`, `ollama pull mistral`).
- Ollama must listen on all interfaces so containers can reach it: `OLLAMA_HOST=0.0.0.0:11434 ollama serve` (default `127.0.0.1` binding rejects container traffic).
- Works on Colima (`colima start --cpu 4 --memory 8`) without Docker Desktop.

#### Config overrides when running under docker-compose

These are existing `Settings` fields (`app/config.py`); override via environment in the compose file's `api` service, not by editing defaults. Local (non-Docker) runs keep the `localhost` defaults.

| Config field      | Local default            | docker-compose value (set on `api` service) |
| ----------------- | ------------------------ | ------------------------------------------- |
| `qdrant_url`      | `http://localhost:6333`  | `http://qdrant:6333`                        |
| `ollama_base_url` | `http://localhost:11434` | `http://host.docker.internal:11434`         |

The Streamlit `ui` service needs the FastAPI base URL pointed at `http://api:8000` (via whatever env var the UI adapter uses for the API endpoint). Document each override in `.env.example` with a comment noting the Docker vs. local distinction.

Definition of done:

- Repo is presentation-ready
- A reviewer can clone, follow README, and have a working system in under 15 minutes
- `docker compose up` starts Qdrant + FastAPI + Streamlit; with host Ollama running, the Streamlit demo answers an end-to-end query

---

#### Cloud deployment (AWS)

The local compose above is for development. The production topology is different — **no local Qdrant, no Ollama** —
and is documented separately in [`docs/aws_deployment_diagram.md`](aws_deployment_diagram.md). Summary:

- **Embeddings** → AWS Bedrock Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`, 1024-dim, `us-east-1`);
  selected by `embedding_backend=bedrock`.
- **Generation** → first-party Anthropic API (Claude Haiku).
- **Vectors** → Qdrant Cloud (collection `ph_law-titan1024`); SQLite + BM25 baked into the image as seed artifacts.
- **Runtime** → one image, two entrypoints (FastAPI `api` + Streamlit `ui`) on Fargate behind an ALB; secrets via
  Secrets Manager / task role, never baked.
- **Local cloud-smoke** → `docker compose -f docker-compose.cloud.yaml up --build` with
  `RAGLAB_ENV_FILE=.env.cloud-gate` and AWS creds mounted.

Staged rollout (Phase 1 cloud seams → Phase 2 zero-Ollama gate → Phase 3 Dockerfile + ECR → Phase 4 CDK) is tracked in
`docs/aws_deployment_diagram.md`.

## Step-by-Step Implementation Order

1. Scaffold folders and packages
2. Create `pyproject.toml` (Python 3.11+, all deps)
3. Create `config.py`
4. Create `db.py` with migrations
5. Create CLI and FastAPI stubs
6. Create Streamlit stub
7. Build source YAML allowlist
8. Implement fetcher
9. Implement `pdf_parser.py` and `html_parser.py`
10. Implement normalizer and storage
11. Implement sync orchestrator
12. Stand up Qdrant via Docker
13. Implement chunker (LlamaIndex SentenceSplitter)
14. Implement embedder (Ollama)
15. Implement vector_store (Qdrant)
16. Implement bm25_store
17. Implement index_service
18. Implement dense_retriever and sparse_retriever
19. Implement hybrid_retriever (RRF)
20. Implement reranker
21. Implement context_builder and prompts
22. Implement llm_client and answer_service
23. Wire CLI `ask` command end-to-end
24. Build Streamlit UI
25. Wire FastAPI routes
26. Build eval dataset
27. Implement RAGAS scorer and eval runner
28. Write tests and docs
29. Add `conversations` and `conversation_turns` migrations to `db.py`
30. Implement `app/conversation/session.py` and `query_rewriter.py`
31. Update `answer_service.py` for session-aware pipeline
32. Update CLI `raglab ask` with `--session` option
33. Update FastAPI `/query/ask` for threaded sessions
34. Update Streamlit UI for multi-turn chat state

---

## Config System

All config lives in `app/config.py`, loaded from `.env` via pydantic-settings.

```python
class Settings(BaseSettings):
    # Paths
    db_path: str = "data/sqlite/raglab.db"
    raw_data_dir: str = "data/raw"
    normalized_data_dir: str = "data/normalized"
    qdrant_path: str = "data/qdrant"
    bm25_path: str = "data/bm25"
    source_config_path: str = "sources/ph_law_sources.yaml"
    eval_dataset_path: str = "data/eval_dataset.jsonl"

    # Models
    embedding_backend: Literal["ollama", "bedrock"] = "ollama"
    embedding_model: str | None = None  # backend default when unset
    embedding_dim: int | None = None     # backend/model default when unset
    llm_model: str = "mistral"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_backend: Literal["minilm", "qwen3"] = "qwen3"
    qwen3_reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # Chunking
    chunk_size: int = 256
    chunk_overlap: int = 32

    # Retrieval
    dense_top_k: int = 30
    sparse_top_k: int = 10
    rerank_top_n: int = 8
    max_distance: float = 0.5
    min_chunks_for_answer: int = 1
    consolidated_dedup_enabled: bool = True

    # Qdrant
    qdrant_collection: str = "ph_law"

    # Misc
    request_timeout: int = 30
    debug: bool = False
    log_level: str = "INFO"
```

---

## Data Model

### `documents`

One row per logical source document.

| Field         | Type    | Notes                                               |
| ------------- | ------- | --------------------------------------------------- |
| `doc_id`      | TEXT PK | Derived from source_id + URL hash                   |
| `source_id`   | TEXT    | From YAML `source_id` field                         |
| `title`       | TEXT    | From YAML or extracted from document                |
| `url`         | TEXT    | Source URL                                          |
| `doc_type`    | TEXT    | `statute`, `sc_decision`, `constitution`, `other`   |
| `file_format` | TEXT    | `html`, `pdf`                                       |
| `category`    | TEXT    | e.g., `civil_law`, `criminal_law`, `constitutional` |
| `tags_json`   | TEXT    | JSON array of tags                                  |
| `enabled`     | INTEGER | 1 = active source                                   |
| `created_at`  | TEXT    | ISO8601                                             |
| `updated_at`  | TEXT    | ISO8601                                             |

### `document_versions`

One row per fetched version of a document.

| Field                   | Type    | Notes                                        |
| ----------------------- | ------- | -------------------------------------------- |
| `version_id`            | TEXT PK | UUID                                         |
| `doc_id`                | TEXT FK | → documents                                  |
| `fetched_at`            | TEXT    | ISO8601                                      |
| `http_status`           | INTEGER | HTTP response code                           |
| `content_hash`          | TEXT    | SHA-256 of normalized text                   |
| `content_length`        | INTEGER | Char count of normalized text                |
| `raw_path`              | TEXT    | Path under `data/raw/`                       |
| `normalized_path`       | TEXT    | Path under `data/normalized/`                |
| `extraction_method`     | TEXT    | `trafilatura`, `pdfplumber`, `beautifulsoup` |
| `changed_from_previous` | INTEGER | 1 if content changed                         |

### `chunks`

One row per chunk version.

| Field            | Type    | Notes                                             |
| ---------------- | ------- | ------------------------------------------------- |
| `chunk_id`       | TEXT PK | UUID                                              |
| `doc_id`         | TEXT FK | → documents                                       |
| `version_id`     | TEXT FK | → document_versions                               |
| `chunk_index`    | INTEGER | Position in document                              |
| `text`           | TEXT    | Chunk content                                     |
| `char_count`     | INTEGER |                                                   |
| `token_estimate` | INTEGER | Rough estimate                                    |
| `qdrant_id`      | TEXT    | Qdrant point ID for deletion                      |
| `metadata_json`  | TEXT    | title, url, doc_type, category, tags, chunk_index |
| `created_at`     | TEXT    | ISO8601                                           |

### `sync_runs`

| Field             | Type    |
| ----------------- | ------- |
| `sync_run_id`     | TEXT PK |
| `started_at`      | TEXT    |
| `completed_at`    | TEXT    |
| `status`          | TEXT    |
| `scanned_count`   | INTEGER |
| `changed_count`   | INTEGER |
| `unchanged_count` | INTEGER |
| `failed_count`    | INTEGER |

### `conversations`

| Field        | Type    | Notes          |
| ------------ | ------- | -------------- |
| `session_id` | TEXT PK | UUID           |
| `created_at` | TEXT    | ISO8601        |
| `title`      | TEXT    | Optional label |

### `conversation_turns`

| Field                   | Type    | Notes                                           |
| ----------------------- | ------- | ----------------------------------------------- |
| `turn_id`               | TEXT PK | UUID                                            |
| `session_id`            | TEXT FK | → conversations                                 |
| `turn_index`            | INTEGER | Position in session (0-based)                   |
| `question`              | TEXT    | Original user question                          |
| `rewritten_question`    | TEXT    | Rewritten standalone query (may equal question) |
| `answer`                | TEXT    | Generated answer                                |
| `retrieved_chunks_json` | TEXT    | JSON array of chunk IDs used                    |
| `created_at`            | TEXT    | ISO8601                                         |

### `schema_migrations`

| Field         | Type       |
| ------------- | ---------- |
| `version`     | INTEGER PK |
| `applied_at`  | TEXT       |
| `description` | TEXT       |

---

## Source Config

`sources/ph_law_sources.yaml` structure:

```yaml
sources:
  - source_id: constitution_1987
    title: "1987 Constitution of the Philippines"
    url: "https://www.officialgazette.gov.ph/constitutions/1987-constitution/"
    doc_type: constitution
    file_format: html
    category: constitutional
    tags: [constitution, fundamental_law]
    enabled: true

  - source_id: civil_code
    title: "Civil Code of the Philippines (RA 386)"
    url: "https://www.lawphil.net/statutes/repacts/ra1950/ra_386_1950.html"
    doc_type: statute
    file_format: html
    category: civil_law
    tags: [civil_code, obligations, contracts, property]
    enabled: true
```

### Starter Corpus Themes

Prioritize sources that represent the core of Philippine law:

- **Constitutional law** — 1987 Constitution
- **Civil law** — Civil Code (RA 386), Family Code (EO 209)
- **Criminal law** — Revised Penal Code (Act 3815)
- **Special laws** — Anti-VAWC (RA 9262), Cybercrime Prevention Act (RA 10175), Data Privacy Act (RA 10173), IP Code (RA 8293)
- **Landmark SC decisions** — 5–10 decisions from SC E-Library covering major constitutional or civil law issues

Target: ~30 sources in V1.

### Corpus Expansion Roadmap (criminal law)

Corpus growth is phased and controlled — not "all criminal law forever." Each phase is a deliberate, bounded expansion with its own eval/OOS-moat review, because adding sources turns previously out-of-scope eval questions in-scope and can silently shift abstention/leak numbers.

**Principle — manifest truth vs. retrieval behavior.** `amends`/`repeals`/`status` are manifest facts. They do *not* automatically change what the retriever prefers:
- Edge expansion uses only `amends` and `implements` (`app/retriever/edges.py`); `repeals` is recorded metadata only.
- Amendment laws with `amends` enter amendment-aware chunking: quoted inserted Article/Section markers are structural only in those documents, and inserted provisions keep amendment `source_id` provenance while their `provision_id` is emitted in the target law namespace. Use `amends_namespace` when a document amends multiple sources and the inserted text targets one namespace.
- "Prefer the newer wording" happens solely through `sources/provision_supersession.yaml` (provision-level reorder, off by default via `prefer_operative`), and only when both base and operative chunks already retrieve. Supersession rules match exact `provision_id` values plus `source_id`; same-number amendments intentionally share the target provision_id with the base source.
- Retrieval suppression is driven by a single field, `operability_action` (`hide`/`show`/`flag`), computed at index time and filtered on by both retrieval arms (`vector_store.operative_filter`, `sparse_retriever`). Its default is derived from the **document** `status` (so `repealed`/`superseded` whole documents are hidden), and it can be overridden **per provision** via `sources/provision_status.yaml` for whole-provision repeal/reclassification (e.g. RPC Art 335 → RA 8353 Art 266-A) or for curated partial amendments using exact `unit_labels` scoped by `source_id` (e.g. RA 10640 rewrote §21 chapeau/items (1)-(3) while items (4)-(8) remain operative). Leaf-scoped overrides stamp hidden leaves with `operability_action: hide` and surviving siblings with `parent_has_hidden_leaves: 1`, which makes parent expansion keep fragments rather than swap in parent text containing hidden leaves. Document `status` is never overwritten by an override; provision state lives in `provision_status`/`operability_action` beside it. Edits to `provision_status.yaml` are applied at index time and require `raglab reindex`. Same-id amendment collisions must become `provision_supersession.yaml` rules, never unscoped `provision_status.yaml` overrides.
- Metadata convention: `build_source_metadata()` omits falsy routing keys such as empty `amends` and unset `amends_namespace`. This prevents a one-time mass reindex for non-amendments while still making amendment manifest edits Tier B drift that re-chunks unchanged text.
- Per-provision amendment timelines are built read-only from `chunks.metadata_json` by `app/indexing/amendment_timeline.py`. The primary identity is each chunk's `provision_id`; path-less inserted provisions resolve into a path-scoped base provision only on exact or unique target-namespace matches. Ambiguous path-less insertions, same-date insertion collisions, and missing approval dates are diagnostics, not guessed ordering. `raglab timeline <fragment>` inspects matching timelines and `raglab timeline --summary` reports corpus totals plus diagnostic counts. Timeline data feeds mechanical consolidation, but the timeline builder itself remains read-only.
- Mechanical consolidation (`app/indexing/consolidation.py`) intentionally changes retrieval behavior at **reindex** time only. Bucketed provisions must have a base entry, exactly one non-partial insertion, length ratio 0.7-1.5, no matching `provision_status.yaml` override, and a dry-run preflight whose recomputed partial flag agrees with stored chunk metadata. Consolidation splices the amendment's full restatement into the base text before both child chunking and parent extraction, appends inline `[as amended by ...]` provenance, stamps consolidated payload metadata on base chunks, and hides the duplicate amendment insertion chunks with `operability_action: hide`. Full reindex builds one plan from the pre-reindex snapshot and enforces coherence after indexing; doc-scoped reindex auto-expands to paired base/amendment docs so one side is never refreshed alone. Sync does not trigger consolidation and writes no new `document_versions` rows for it.

So the rule when adding an amended penal law: add the amendment too; set `amends` and, for multi-target amendments, `amends_namespace`; for a *wholly* dead base provision add a bare `provision_status.yaml` override; for a partial amendment only add a `source_id` + exact `unit_labels` override when the replacement text is indexed and operative; add a `provision_supersession` reorder rule only for high-risk both-retrieved pairs (trace-gated).

**Phase A — Core criminal law + common SPLs (current expansion).**
- RPC (Act 3815) + key amendments (RA 10951)
- Sentencing modifiers: ISL (Act 4103), Probation (PD 968 + RA 10707), Juvenile Justice (RA 9344 + RA 10630)
- Common SPLs: drugs (RA 9165 + RA 10640), VAWC (RA 9262), cybercrime (RA 10175), BP 22, child abuse (RA 7610), trafficking (RA 9208 + RA 10364 + RA 11862), anti-graft (RA 3019 + RA 10910), plunder (RA 7080), firearms (RA 10591), carnapping (RA 10883), hazing (RA 8049 + RA 11053), child pornography → OSAEC (RA 9775 `repealed` → RA 11930), Safe Spaces (RA 11313), statutory rape (RA 11648)
- Current-law correctness sources: RA 9346 (death-penalty prohibition — global penalty modifier, standalone + curated supersession rules)
- Deferred within A: RA 7658 / RA 9231 (child-labor amendments — drift toward fenced labor/social-legislation territory)

**Phase B — Public-order / national-security crimes.** Anti-Terrorism Act, rebellion/sedition special laws, Human Security Act predecessors.

**Phase C — Financial / commercial penal laws.** AMLA, banking secrecy, securities-regulation offenses.

**Phase D — Election, tax, customs, environmental, local-government offenses.** Omnibus Election Code offenses, NIRC/Customs penal provisions, environmental penal laws, LGC offenses.

**Phase E — Historical predecessors and repeal chains.** Older repealed predecessors and deeper amendment chains for jurisprudential/temporal-retrieval value.

Phases B–E are explicitly out of the current OOS moat's in-scope set; expanding into them requires re-baselining the eval suite. The current expansion is Phase A only.

---

## Hybrid Retrieval Design

### Dense Retrieval (Qdrant)

- Embed query via `nomic-embed-text` (768 dimensions)
- Query Qdrant collection with cosine similarity
- Retrieve top-k = 30 candidates with scores and metadata

### Sparse Retrieval (BM25)

- LlamaIndex `BM25Retriever` built from all indexed chunks at sync time
- Persisted to `data/bm25/` as a serialized index
- Retrieve top-k = 10 candidates with BM25 scores

### RRF Fusion

Reciprocal Rank Fusion merges the two ranked lists:

```
RRF_score(doc) = Σ 1 / (k + rank_i(doc))
```

where `k = 60` (standard constant), and `rank_i` is the position in each retriever's result list. Documents appearing in both lists get a boosted combined score.

### Reranking

After RRF fusion, merged candidates are re-scored by the configured selector:

- Input: `(query, chunk_text)` pairs
- Eval-quality default: `reranker_backend=qwen3` using `Qwen/Qwen3-Reranker-0.6B`
- Latency-sensitive fallback: `reranker_backend=minilm` using `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Output: relevance score per pair
- Top `rerank_top_n` (default: 8) form the post-rerank/pre-parent-expansion list used by answer gates
- Parent expansion and consolidated dedup then produce the final selected generation/inspection context

### Why This Matters for Legal Text

| Query type                         | Dense handles | BM25 handles    |
| ---------------------------------- | ------------- | --------------- |
| "What are the elements of estafa?" | ✓ (semantic)  | ✗               |
| "Republic Act 10173 section 16"    | ✗             | ✓ (exact match) |
| "G.R. No. 12345"                   | ✗             | ✓ (exact match) |
| "rights of an accused person"      | ✓ (semantic)  | partial         |

---

## Chunking Design

- Use LlamaIndex `SentenceSplitter`
- Target chunk size: ~256 tokens
- Overlap: 32 tokens
- Preserves sentence boundaries
- Chunk metadata attached at index time:
  - `doc_id`, `version_id`, `source_url`, `title`, `doc_type`, `category`, `tags`, `chunk_index`

---

## Generation Design

System prompt (grounding):

```
You are a Philippine law assistant. Answer questions based only on the
provided legal sources. Do not invent statutes, case citations, or legal
interpretations not present in the context. When citing, reference the
source number (e.g., [1], [2]). If the provided context does not contain
sufficient information to answer the question, say: "I don't have enough
information from the available sources to answer this question."
```

Response structure:

- Direct answer
- Inline citations by reference number
- Sources section listing title, URL, article/section where applicable

Abstention gate: if fewer than `min_chunks_for_answer` chunks survive the `max_distance` filter, skip generation and return the insufficient-evidence response directly.

---

## Eval Strategy

### Dataset Design

The tracked eval dataset currently has 70 questions across categories. Keep the out-of-scope slice stable when expanding the corpus so abstention metrics remain comparable across runs.

| Category     | Count | Description                                        |
| ------------ | ----- | -------------------------------------------------- |
| Factual      | 34    | Specific article, section, or RA lookup            |
| Paraphrase   | 8     | Same meaning as a factual query, different wording |
| Synthesis    | 10    | Requires combining context from 2+ sources         |
| Ambiguous    | 6     | May be partially answerable                        |
| Out-of-scope | 12    | Should trigger abstention                          |

### RAGAS Metrics

| Metric            | What it measures                                          |
| ----------------- | --------------------------------------------------------- |
| Faithfulness      | Is the answer supported by the retrieved context?         |
| Answer relevance  | Does the answer actually address the question?            |
| Context precision | Are the top-ranked chunks relevant to the question?       |
| Context recall    | Does the retrieved context cover the ground-truth answer? |

### Eval Dataset Format

```jsonl
{
  "question": "What is the prescriptive period for filing a criminal case for estafa?",
  "ground_truth": "Under Article 90 of the Revised Penal Code, the prescriptive period for estafa depends on the penalty attached to the offense.",
  "expected_sources": [
    "revised_penal_code"
  ],
  "category": "factual"
}
```

---

## Streamlit UI Design

### Chat Tab

- Text input for questions
- Answer display with cited sources as clickable links
- Expandable "Debug" section showing retrieved chunks, distances, rerank scores

### Sources Tab

- Table of all indexed documents (title, doc_type, category, last synced, status)
- Filter by doc_type and category

### Settings Sidebar

- LLM model selector (reads available Ollama models)
- top-k and rerank_top_n sliders
- Debug mode toggle
- `Sync Now` button (calls FastAPI `/sync`)

---

## FastAPI Endpoints

| Method | Path                  | Description                                            |
| ------ | --------------------- | ------------------------------------------------------ |
| GET    | `/health`             | Health check; verifies Qdrant and Ollama are reachable |
| POST   | `/query/ask`          | Ask a question; returns answer + citations             |
| GET    | `/documents`          | List all documents with sync status                    |
| GET    | `/documents/{doc_id}` | Single document metadata                               |
| POST   | `/sync`               | Trigger sync as background task                        |

---

## Local Setup Requirements

- Python 3.11+
- Docker (for Qdrant)
- Ollama installed and running
- uv or pip

### First Run

```bash
# 1. Clone and install
git clone https://github.com/your-username/ph-law-rag.git
cd ph-law-rag
uv sync

# 2. Start Qdrant
docker-compose up -d

# 3. Pull Ollama models
ollama pull mistral
ollama pull nomic-embed-text

# 4. Configure
cp .env.example .env

# 5. Initialize
raglab init

# 6. Sync corpus
raglab sync

# 7. Ask a question
raglab ask "What are the elements of a valid contract under the Civil Code?"

# 8. Run Streamlit
streamlit run app/ui/app.py

# 9. Run evals
raglab eval
```

---

## Tradeoffs and Constraints

**LlamaIndex over LangChain** — LlamaIndex has more opinionated, better-abstracted primitives for document retrieval. For a RAG project (not a general agent), it's the right choice. LangChain would be appropriate if the project later needed complex multi-step tool use or agent loops.

**Qdrant over ChromaDB** — Qdrant supports hybrid search natively (dense + sparse in one query), has a stable concurrent-safe architecture, and runs well in Docker. ChromaDB's PersistentClient has known issues with concurrent access. The tradeoff is requiring Docker for local setup.

**RAGAS over custom eval scoring** — RAGAS provides semantic scoring via LLM-graded metrics, which is significantly more meaningful than keyword matching. The tradeoff is that RAGAS requires a working LLM at eval time (uses Ollama), adding an extra dependency to the eval pipeline.

**Trafilatura over BeautifulSoup** — Trafilatura strips navigation boilerplate, which is the primary quality problem in the original project. The tradeoff is a less familiar library with slightly less control over what gets extracted.

**Local-first with Ollama** — No cloud API keys required. The tradeoff is lower model quality than GPT-4 or Claude. For a portfolio project demonstrating pipeline design, this is the right call. The LLM backend is pluggable via config if cloud inference is desired later.

**Document-level re-indexing** — Same tradeoff as the original: when a document changes, all its vectors are deleted and the entire document is re-chunked and re-embedded. Simpler than chunk-level diffing and fast enough for a small corpus.

---

### Milestone 8: Conversation Context Management

Goal: multi-turn conversations work end-to-end — follow-up questions are resolved against prior context before retrieval.

Build:

**New SQLite tables (new migration in `db.py`):**

- `conversations` — `session_id` (PK), `created_at`, `title` (optional label)
- `conversation_turns` — `turn_id` (PK), `session_id` (FK), `turn_index`, `question`, `rewritten_question`, `answer`, `retrieved_chunks_json`, `created_at`

**New config fields in `app/config.py`:**

```python
max_conversation_turns: int = 5       # history window passed to rewriter
enable_query_rewriting: bool = True   # toggle rewriting off for debugging
```

**New module `app/conversation/`:**

- `session.py` — `create_session(conn) -> str`, `get_history(conn, session_id, limit) -> list[dict]`, `append_turn(conn, session_id, turn_data) -> str`
- `query_rewriter.py` — `rewrite_query(question: str, history: list[dict]) -> str`: calls Ollama LLM with a short prompt that resolves pronouns and ellipsis ("what about that?", "and section 5?") into a self-contained query; returns original question unchanged if `enable_query_rewriting = False` or history is empty

**Changes to existing files:**

- `answer_service.py` — accept optional `session_id: str | None`; if provided, load history, rewrite query, run pipeline on rewritten query, persist turn to `conversation_turns`
- `app/cli/main.py` — `raglab ask` gains `--session TEXT` option; if omitted, creates a new session each invocation (stateless); if provided, loads and continues that session
- `app/api/main.py` — `POST /query/ask` request body gains optional `session_id`; response includes `session_id` so clients can thread turns
- `app/ui/app.py` — maintain `session_id` in `st.session_state`; display full conversation history in the chat tab; "New conversation" button resets state

**Query rewriting prompt (in `prompts.py`):**

```
Given the following conversation history and a follow-up question, rewrite
the follow-up as a standalone question that can be understood without the
history. Do not answer the question — only rewrite it. If the follow-up is
already self-contained, return it unchanged.

History:
{history}

Follow-up: {question}
Standalone question:
```

Definition of done:

- `raglab ask --session abc "what about section 5?"` correctly resolves "section 5" against prior turns in session `abc`
- Streamlit chat tab maintains conversation state across turns in the browser
- `POST /query/ask` with `session_id` returns a threaded response
- Sessions with no history bypass rewriting (no unnecessary LLM call)
- `max_conversation_turns` caps how much history is passed to the rewriter

Key constraints:

- Rewriting is a separate LLM call before retrieval — keep it short (use a fast/small model or the same Ollama model with a low token budget)
- Never pass raw history into the retrieval prompt — only the rewritten standalone query goes to the retriever
- History is stored in SQLite, not in-memory — sessions survive process restarts

---

## Future Feature: Exam Mode and Bar Readiness

Exam Mode is a separate product workflow for bar takers and law students. It should not be treated as ordinary legal Q&A. The goal is to simulate bar-style practice, grade user-written answers against private references/rubrics, and return actionable feedback.

### Product Concept

The user selects a subject, year range, difficulty, and number of questions. The app presents bar-style questions under timed conditions. After the user submits an answer, the system produces a practice score plus feedback explaining where the answer earned points, where it lost points, and what legal analysis was missing.

This feature is a strong candidate for a later commercial or hosted version because it is naturally meterable: one submitted answer roughly equals one billable grading event.

### Data Sources

- Supreme Court bar questionnaires are public and can be used as exam prompts where allowed by source terms.
- Supreme Court questionnaires are available for recent bar years, including questionnaires up to 2025.
- Suggested answers, such as UP Law Center suggested answers, may be purchased and used privately as ground truth/reference material.
- The currently identified suggested-answer range is 2012 through 2022. Later questionnaires may exist without corresponding purchased suggested answers yet.
- Do not publish copyrighted suggested-answer text unless the license expressly permits it.
- Public reports may publish aggregate metrics, category scores, and failure analysis without redistributing private answer keys.

This creates two related but distinct datasets:

1. **Question-only dataset** — public bar questions, potentially covering 2012 through 2025, useful for retrieval smoke tests, mock exams, and manual answer review.
2. **Graded eval dataset** — questions with private suggested answers/rubrics, currently expected to cover 2012 through 2022, useful for scoring model answers and user-written exam answers.

Suggested private file organization:

```text
data/evals/bar_exam/
  questions_2012_2025.jsonl                   # public prompts/metadata where allowed
  suggested_answers_2012_2022.private.jsonl   # purchased/private, gitignored
  rubrics_2012_2022.private.jsonl             # derived from suggested answers, gitignored
  eval_items_2012_2022.private.jsonl          # joined question + rubric IDs, gitignored
```

Suggested metadata fields:

```json
{
  "question_id": "bar_2025_pil_prior_restraint_abc_news",
  "year": 2025,
  "subject": "Political and Public International Law",
  "question_number": "I",
  "source": "Supreme Court Bar Questionnaire",
  "has_suggested_answer": false,
  "suggested_answer_source": null,
  "category": "application",
  "skills": ["issue_spotting", "doctrine_application", "synthesis"],
  "expected_sources": ["constitution_1987", "prior_restraint_cases"]
}
```

Suggested question IDs:

```text
bar_2025_pil_prior_restraint_abc_news
bar_2024_civil_oblicon_q03
bar_2022_crim_special_laws_q05
```

### Scoring Model

Use rubric-based grading, not simple semantic similarity. A typical rubric should break points down by:

- issue spotting
- rule accuracy
- application to facts
- conclusion
- citation/source support where appropriate
- clarity and organization

Example rubric shape:

```json
{
  "question_id": "bar_2025_pil_prior_restraint_abc_news",
  "max_score": 10,
  "criteria": [
    {
      "name": "Identifies prior restraint / freedom of press issue",
      "points": 2
    },
    { "name": "States the presumption against prior restraint", "points": 2 },
    {
      "name": "Discusses narrow exceptions / clear and present danger",
      "points": 2
    },
    {
      "name": "Applies doctrine to DOJ prohibition and violence facts",
      "points": 2
    },
    { "name": "Reaches and explains the correct conclusion", "points": 1 },
    { "name": "Clear bar-style structure", "points": 1 }
  ]
}
```

### Feedback Output

The grader should return structured JSON so UI and reports can be built reliably:

```json
{
  "score": 6.5,
  "max_score": 10,
  "issue_spotting": 1.5,
  "rule_accuracy": 2,
  "application": 1.5,
  "conclusion": 1,
  "clarity": 0.5,
  "what_went_well": [],
  "what_cost_points": [],
  "missing_analysis": [],
  "wrong_or_unsupported_points": [],
  "study_recommendations": []
}
```

### Architecture Notes

- Keep Exam Mode separate from the general ask pipeline.
- Runtime grading should usually not need live retrieval because the question, reference answer, and rubric are already known.
- Use RAG only when the feature needs to show source support, explain a doctrine, or debug a grading result.
- Use one cloud judge call per submitted answer where possible.
- Cache grading results by a hash of `question_id + rubric_version + user_answer`.
- Precompute rubrics and reference summaries offline.
- Use local models for low-risk tasks such as study-plan formatting, but prefer a stronger cloud model for final grading if accuracy matters.

### Cost Controls

- Grade only on submit, never while the user is typing.
- Limit answer length per question.
- Cache repeated submissions.
- Keep a free tier limited by number of graded answers.
- Consider paid grading credits or a subscription plan.
- Batch analytics and cohort reports offline.
- Keep infrastructure simple: a single VPS plus SQLite/Postgres and object storage is enough for an MVP.

### Commercial Segments

- Bar takers: roughly 10k-11.5k examinees per recent year; high urgency and clear willingness to prepare.
- Law students: larger surrounding market; useful for subject drills and semester review.
- Practicing lawyers: potentially higher willingness to pay, but the quality and liability bar is much higher.

Initial wedge should likely be bar takers or law students, not practicing lawyers. Exam Mode offers measurable progress and lower legal-risk framing than a production legal-advice assistant.

### Sequencing

Do not build Exam Mode before the retrieval system is stable. First improve the current retrieval metrics, especially:

- multi-hop/synthesis retrieval
- provision-aware indexing
- query decomposition
- neighboring chunk/section expansion
- debug traces showing which query found which chunk

Once the current eval set is stable, start with a small private bar-exam slice:

1. One subject, such as Political Law or Civil Law
2. 30-50 curated questions
3. Private suggested answers and hand-authored rubrics
4. Cloud-judge grading
5. Manual audit of scores and feedback quality

Definition of done for an MVP:

- User can start a timed practice session
- User can submit written answers
- System returns a structured practice score and feedback
- Results are saved per user/session
- Aggregate per-subject and per-skill weakness reports are available
- Private answer keys/rubrics are gitignored and never exposed in public artifacts

---

## Phase 2 Ideas

After V1:

- Metadata filtering in Qdrant queries (filter by doc_type, category, RA number)
- Query routing: classify question as constitutional / civil / criminal and filter corpus
- Chunk-level incremental indexing
- Support for scanned PDF OCR (Tesseract)
- Scheduled sync
- OpenAI / Anthropic / Bedrock LLM backend via LlamaIndex adapters
- LangChain integration for agent-based multi-hop legal research
- Comparative mode: "how does RA 10173 relate to RA 10175 on data privacy?"
- Export answers as formatted legal memos

---

## Ongoing Review Instructions

When reviewing code:

1. Compare implementation against this plan
2. Flag unnecessary complexity — LlamaIndex abstractions should simplify, not obscure
3. Keep business logic out of the Streamlit and FastAPI adapters
4. Preserve incremental-sync architecture
5. Preserve local-first design
6. Prefer explicit retrieval trace in debug mode over silent failures

If the current implementation differs from this plan, note whether the difference is:

- Acceptable simplification
- Technical debt
- Bug
- Scope creep
- Worthwhile improvement

---

## Future Feature: React + Vite Frontend (Corpus Browser)

A polished React frontend to eventually replace the Streamlit UI. Primary Phase-1 goal: a **source/corpus browser** — browse indexed law documents, filter by `doc_type`/`category`, view normalized text + sync status. This is a clean adapter swap: FastAPI is untouched except for one new endpoint; business logic stays out of the frontend.

### Stack (decided)

- **Vite + React + TypeScript** (TS non-negotiable — portfolio signal).
- **React Router** — list → detail routing.
- **TanStack Query** — data fetching/caching/loading-states for the GETs.
- **TanStack Table** — the document list is a filter-by-`doc_type`/`category` table.
- **Tailwind + shadcn/ui** — Radix primitives copied into the repo; polished tables/inputs fast.
- **Vite dev proxy** `/api → http://localhost:8000` — sidesteps CORS in dev (no FastAPI middleware). Prod build served static via the compose `ui` service.

shadcn/ui setup notes: needs Tailwind configured **and** path aliases (`@/*`) in both `tsconfig.json` and `vite.config.ts` before `npx shadcn@latest init`. Components copied into `src/components/ui/` on demand; Phase 1 pulls `table button badge input select scroll-area`.

### Backend gap (gating dependency)

The corpus browser needs to view a document, but no endpoint serves the normalized text. Build **`GET /documents/{doc_id}`** (metadata + contents of `document_versions.normalized_path`) first, and `curl`-verify before touching React. The file-read goes in a `db.py`/service function; the route stays a thin adapter. (This endpoint is already listed in the FastAPI Endpoints table but is currently unbuilt.)

### Folder layout

```
frontend/                      # sibling to app/, own package.json
  src/
    api/client.ts              # fetch wrapper + types (Document, DocumentDetail)
    routes/
      DocumentsList.tsx        # table + filters + Sync button
      DocumentDetail.tsx       # metadata header + normalized text pane
    components/                # shared + components/ui/ (shadcn)
    App.tsx                    # router
    main.tsx
  vite.config.ts               # proxy /api → :8000
  package.json
```

### Phasing

- **Phase 1:** corpus browser — list + filters + detail view + Sync button.
- **Phase 2:** chat + citations on `/query/ask`, then retire Streamlit. **Retire-on-parity, not day-1** — keep Streamlit (which currently also serves chat) alive until the React chat ships, so the demo is never broken. Then repoint the compose `ui` service from Streamlit to the React static build.

### Build order

1. `GET /documents/{doc_id}` + `db.py` reader → curl check.
2. `npm create vite@latest frontend -- --template react-ts`; install deps; Tailwind + shadcn init; path aliases.
3. `vite.config.ts` proxy `/api → http://localhost:8000`.
4. `src/api/client.ts` — typed `Document` / `DocumentDetail` + fetch wrappers.
5. DocumentsList (TanStack Table + `doc_type`/`category` filters + Sync).
6. DocumentDetail (metadata header + scrollable text pane).

### Open question / tradeoff (deferred)

A React rebuild competes for time with the bar-exam grader, which is a rarer portfolio differentiator than a frontend. Treat this as polish, sequence it accordingly.
