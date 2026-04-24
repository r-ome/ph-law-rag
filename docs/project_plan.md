# Philippine Law RAG — Project Plan

This is the project reference document for `ph-law-rag`.

- Update this file whenever implementation meaningfully changes.
- Use this plan as the default source of truth for architecture, scope, and priorities during implementation and review.
- If the code intentionally differs from this plan, document the reason in review notes or adjacent docs.

See also: `docs/current_status.md`

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

| Concern | Tool | Reason |
|---|---|---|
| RAG orchestration | LlamaIndex | Purpose-built for document retrieval pipelines; first-class hybrid retrieval, reranking, and eval support |
| LLM | Ollama (mistral or llama3) | Local, free, model-swappable via config |
| Embeddings | Ollama `nomic-embed-text` | Local, high-quality 768-dim embeddings; swap via config |
| Vector store | Qdrant (local Docker) | Native hybrid search (dense + sparse in one query), metadata filtering, concurrent-safe |
| Sparse index | LlamaIndex BM25Retriever | Exact-match keyword retrieval; pairs with dense for hybrid |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Query-time cross-encoder rescoring; significantly improves ranking quality |
| PDF ingestion | `pdfplumber` via LlamaIndex | Better table and layout handling than PyPDF2 |
| HTML ingestion | `trafilatura` | Strips navigation boilerplate better than BeautifulSoup |
| Evals | RAGAS | Semantic eval scoring: faithfulness, answer relevance, context precision, context recall |
| Frontend | Streamlit | Fast, Python-native, enough for a portfolio demo UI |
| API | FastAPI | Same service modules as Streamlit; thin adapter |
| Config | pydantic-settings | `.env`-driven config with type validation and defaults |
| Metadata / versioning | SQLite | Zero-setup, ships with Python, sufficient for the workload |
| Dependency management | uv | Fast, lockfile-based |
| Testing | pytest | Standard |

---

## Architecture

The system has 5 parts:

### 1. Source Sync Pipeline

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
- Run dense retrieval from Qdrant (top-k = 10 candidates)
- Run BM25 sparse retrieval (top-k = 10 candidates)
- Merge results via Reciprocal Rank Fusion (RRF)
- Re-score merged candidates with cross-encoder reranker
- Apply `max_distance` filter; apply `min_chunks_for_answer` gate
- Build numbered context prompt with source citations
- Generate answer via Ollama LLM
- Return answer + citation list

### 4. Eval Pipeline

- Load eval questions from `data/eval_dataset.jsonl`
- Run each question through the full ask pipeline
- Score results via RAGAS metrics: faithfulness, answer relevance, context precision, context recall
- Save per-question results to `data/eval_results/`
- Print category-level report

### 5. Interface Layer

- **Streamlit app** — chat-style UI with sidebar for settings and source browser
- **FastAPI** — `/health`, `/query/ask`, `/documents`, `/sync` (for the API layer)
- Both call the same shared service modules; no business logic in either adapter

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
│   ├── ingestion/
│   │   ├── fetcher.py       # httpx downloader → FetchResult
│   │   ├── pdf_parser.py    # pdfplumber extraction
│   │   ├── html_parser.py   # trafilatura extraction
│   │   ├── normalizer.py    # whitespace cleanup
│   │   ├── storage.py       # hash compare, disk write, SQLite write
│   │   └── sync.py          # orchestrator: loops sources, calls above
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
    ├── local_setup.md
    └── current_status.md
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
- `sync.py` — orchestrator with per-source status reporting
- SQLite `documents`, `document_versions`, `sync_runs` tables

Definition of done:
- `raglab sync` fetches all enabled sources
- Changed vs. unchanged is tracked and reported
- Re-running sync on unchanged corpus skips all downstream processing

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
- `ragas_scorer.py` — computes RAGAS metrics per question:
  - **Faithfulness** — is the answer grounded in the retrieved context?
  - **Answer relevance** — does the answer address the question?
  - **Context precision** — are the retrieved chunks actually relevant?
  - **Context recall** — does the retrieved context cover the expected answer?
- `report.py` — aggregates scores by question category, prints summary table
- CLI `raglab eval` runs the full cycle

Definition of done:
- `raglab eval` produces per-question RAGAS scores and a category summary
- Results are saved as JSONL for manual review

---

### Milestone 7: Polish and GitHub Readiness

Build:
- `README.md` with setup instructions, demo commands, example output
- `docs/architecture.md` — system design, data flow, package breakdown
- `docs/tradeoffs.md` — design decisions and reasoning
- `docs/local_setup.md` — detailed Qdrant Docker setup, Ollama model pull, first run
- Tests (unit for normalizer, chunker, hash logic; integration for sync and ask pipeline)
- `.env.example` with all configurable values documented
- `docker-compose.yml` for Qdrant

Definition of done:
- Repo is presentation-ready
- A reviewer can clone, follow README, and have a working system in under 15 minutes

---

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
    embedding_model: str = "nomic-embed-text"
    llm_model: str = "mistral"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # Chunking
    chunk_size: int = 256
    chunk_overlap: int = 32

    # Retrieval
    dense_top_k: int = 10
    sparse_top_k: int = 10
    rerank_top_n: int = 5
    max_distance: float = 0.5
    min_chunks_for_answer: int = 2

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

| Field | Type | Notes |
|---|---|---|
| `doc_id` | TEXT PK | Derived from source_id + URL hash |
| `source_id` | TEXT | From YAML `source_id` field |
| `title` | TEXT | From YAML or extracted from document |
| `url` | TEXT | Source URL |
| `doc_type` | TEXT | `statute`, `sc_decision`, `constitution`, `other` |
| `file_format` | TEXT | `html`, `pdf` |
| `category` | TEXT | e.g., `civil_law`, `criminal_law`, `constitutional` |
| `tags_json` | TEXT | JSON array of tags |
| `enabled` | INTEGER | 1 = active source |
| `created_at` | TEXT | ISO8601 |
| `updated_at` | TEXT | ISO8601 |

### `document_versions`

One row per fetched version of a document.

| Field | Type | Notes |
|---|---|---|
| `version_id` | TEXT PK | UUID |
| `doc_id` | TEXT FK | → documents |
| `fetched_at` | TEXT | ISO8601 |
| `http_status` | INTEGER | HTTP response code |
| `content_hash` | TEXT | SHA-256 of normalized text |
| `content_length` | INTEGER | Char count of normalized text |
| `raw_path` | TEXT | Path under `data/raw/` |
| `normalized_path` | TEXT | Path under `data/normalized/` |
| `extraction_method` | TEXT | `trafilatura`, `pdfplumber`, `beautifulsoup` |
| `changed_from_previous` | INTEGER | 1 if content changed |

### `chunks`

One row per chunk version.

| Field | Type | Notes |
|---|---|---|
| `chunk_id` | TEXT PK | UUID |
| `doc_id` | TEXT FK | → documents |
| `version_id` | TEXT FK | → document_versions |
| `chunk_index` | INTEGER | Position in document |
| `text` | TEXT | Chunk content |
| `char_count` | INTEGER | |
| `token_estimate` | INTEGER | Rough estimate |
| `qdrant_id` | TEXT | Qdrant point ID for deletion |
| `metadata_json` | TEXT | title, url, doc_type, category, tags, chunk_index |
| `created_at` | TEXT | ISO8601 |

### `sync_runs`

| Field | Type |
|---|---|
| `sync_run_id` | TEXT PK |
| `started_at` | TEXT |
| `completed_at` | TEXT |
| `status` | TEXT |
| `scanned_count` | INTEGER |
| `changed_count` | INTEGER |
| `unchanged_count` | INTEGER |
| `failed_count` | INTEGER |

### `conversations`

| Field | Type | Notes |
|---|---|---|
| `session_id` | TEXT PK | UUID |
| `created_at` | TEXT | ISO8601 |
| `title` | TEXT | Optional label |

### `conversation_turns`

| Field | Type | Notes |
|---|---|---|
| `turn_id` | TEXT PK | UUID |
| `session_id` | TEXT FK | → conversations |
| `turn_index` | INTEGER | Position in session (0-based) |
| `question` | TEXT | Original user question |
| `rewritten_question` | TEXT | Rewritten standalone query (may equal question) |
| `answer` | TEXT | Generated answer |
| `retrieved_chunks_json` | TEXT | JSON array of chunk IDs used |
| `created_at` | TEXT | ISO8601 |

### `schema_migrations`

| Field | Type |
|---|---|
| `version` | INTEGER PK |
| `applied_at` | TEXT |
| `description` | TEXT |

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

---

## Hybrid Retrieval Design

### Dense Retrieval (Qdrant)

- Embed query via `nomic-embed-text` (768 dimensions)
- Query Qdrant collection with cosine similarity
- Retrieve top-k = 10 candidates with scores and metadata

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

### Cross-Encoder Reranking

After RRF fusion, the top 20 merged candidates are re-scored by a cross-encoder:

- Input: `(query, chunk_text)` pairs
- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Output: relevance score per pair
- Top `rerank_top_n` (default: 5) pass to context builder

### Why This Matters for Legal Text

| Query type | Dense handles | BM25 handles |
|---|---|---|
| "What are the elements of estafa?" | ✓ (semantic) | ✗ |
| "Republic Act 10173 section 16" | ✗ | ✓ (exact match) |
| "G.R. No. 12345" | ✗ | ✓ (exact match) |
| "rights of an accused person" | ✓ (semantic) | partial |

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

40–60 questions across categories:

| Category | Count | Description |
|---|---|---|
| Factual | 15 | Specific article, section, or RA lookup |
| Paraphrase | 10 | Same meaning as a factual query, different wording |
| Synthesis | 10 | Requires combining context from 2+ sources |
| Ambiguous | 8 | May be partially answerable |
| Out-of-scope | 10 | Should trigger abstention |

### RAGAS Metrics

| Metric | What it measures |
|---|---|
| Faithfulness | Is the answer supported by the retrieved context? |
| Answer relevance | Does the answer actually address the question? |
| Context precision | Are the top-ranked chunks relevant to the question? |
| Context recall | Does the retrieved context cover the ground-truth answer? |

### Eval Dataset Format

```jsonl
{
  "question": "What is the prescriptive period for filing a criminal case for estafa?",
  "ground_truth": "Under Article 90 of the Revised Penal Code, the prescriptive period for estafa depends on the penalty attached to the offense.",
  "expected_sources": ["revised_penal_code"],
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

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check; verifies Qdrant and Ollama are reachable |
| POST | `/query/ask` | Ask a question; returns answer + citations |
| GET | `/documents` | List all documents with sync status |
| GET | `/documents/{doc_id}` | Single document metadata |
| POST | `/sync` | Trigger sync as background task |

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
