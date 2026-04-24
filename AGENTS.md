# ph-law-rag — Claude Instructions

See `docs/project_plan.md` for the full architecture, data model, and milestone definitions. That file is the source of truth. Update it when implementation meaningfully diverges.

---

## Milestone 1: Scaffold and Local Runtime

**Goal:** Project boots cleanly, all entry points work.

### What to build

1. **Repo folder structure** — create all directories listed in the Architecture section of the project plan (empty `__init__.py` files where needed to make packages importable).

2. **`pyproject.toml`** — Python 3.11+, define a `raglab` console script entry point, include all project dependencies up front (see stack in project plan).

3. **`app/config.py`** — `Settings` class using `pydantic-settings`, loaded from `.env`. Use the exact field names and defaults from the Config System section in the project plan.

4. **`.env.example`** — one line per config key with its default value and a short comment.

5. **`app/db.py`** — SQLite bootstrap:
   - Connect to `settings.db_path`, creating the file and parent dirs if needed.
   - Create a `schema_migrations` table on first run.
   - Apply migrations in order; skip already-applied versions.
   - Migration 1: create `documents`, `document_versions`, `chunks`, `sync_runs` tables using the exact schema from the Data Model section of the project plan.

6. **CLI (`app/cli.py` or `app/main.py`)** — Typer app, registered as the `raglab` console script. Commands at this milestone are stubs except `init`:
   - `raglab init` — creates `data/raw/`, `data/normalized/`, `data/qdrant/`, `data/bm25/`, `data/sqlite/`, `data/eval_results/` directories; bootstraps the DB by calling `db.py`; prints confirmation.
   - `raglab sync` — stub: prints "sync not yet implemented".
   - `raglab ask` — stub: prints "ask not yet implemented".
   - `raglab eval` — stub: prints "eval not yet implemented".
   - `raglab healthcheck` — stub: prints "healthcheck not yet implemented".
   - `raglab show-config` — prints the current `Settings` as JSON (use `settings.model_dump()`).

7. **`app/api/main.py`** — FastAPI app with a single route:
   - `GET /health` → returns `{"status": "ok"}`.

8. **`app/ui/app.py`** — Streamlit stub:
   - Title: "PH Law RAG"
   - A single `st.write("UI not yet implemented.")` placeholder.

### Definition of done

- `raglab init` runs without error, creates data directories, and bootstraps the DB.
- `raglab show-config` prints the loaded config as JSON.
- `uvicorn app.api.main:app` starts and `GET /health` returns `{"status": "ok"}`.
- `streamlit run app/ui/app.py` opens the browser stub without error.
- Config loads correctly from a `.env` file.
- DB initializes cleanly (no errors on repeated `raglab init` runs — idempotent).

### Key constraints

- Do not start on ingestion, retrieval, or indexing logic yet — those are Milestone 2+.
- Keep all imports lazy where possible so the CLI doesn't fail at startup if optional deps (Qdrant, Ollama clients) are missing.
- No business logic in `app/api/main.py` or `app/ui/app.py` — they are adapters only.
- Use `uv` for dependency management.

---

## Milestone 2: Document Sync and Normalization

**Goal:** `raglab sync` fetches, normalizes, hashes, and versions documents. Re-running on an unchanged corpus skips all processing.

### New config fields to add to `app/config.py`

Add these to `Settings` before starting — they'll be needed throughout this milestone:

```python
raw_data_dir: str = "data/raw"
normalized_data_dir: str = "data/normalized"
source_config_path: str = "sources/ph_law_sources.yaml"
request_timeout: int = 30
```

### New packages to add to `pyproject.toml`

```
httpx
pdfplumber
trafilatura
beautifulsoup4
pyyaml
```

Run `uv add <package>` for each.

### New folders to create

```
app/ingestion/__init__.py   (empty)
```

### What to build (in order)

---

#### 1. `sources/ph_law_sources.yaml` — expand to ~25–30 sources

The file exists but only has 2 entries. Expand it. Cover these themes:
- Constitutional: 1987 Constitution
- Civil law: Civil Code (RA 386), Family Code (EO 209)
- Criminal law: Revised Penal Code (Act 3815)
- Special laws: Anti-VAWC (RA 9262), Cybercrime Prevention Act (RA 10175), Data Privacy Act (RA 10173), IP Code (RA 8293)
- SC decisions: 5–10 decisions from SC E-Library

Also fix the existing typo: `source_id: consitution_1987` → `constitution_1987`.

Each entry must have: `source_id`, `title`, `url`, `doc_type`, `file_format` (`html` or `pdf`), `category`, `tags` (list), `enabled` (bool).

---

#### 2. `app/ingestion/fetcher.py` — HTTP downloader

Returns a `FetchResult` dataclass:

```python
@dataclass
class FetchResult:
    source_id: str
    url: str
    file_format: str   # "html" or "pdf"
    status: str        # "ok" | "failed"
    http_status: int | None
    content: bytes | None
    error: str | None
```

Logic:
- Use `httpx` with a `User-Agent` header (e.g. `"ph-law-rag/1.0"`) and `timeout=settings.request_timeout`
- On HTTP error or exception, set `status="failed"` and populate `error`; never raise
- No retry logic yet — keep it simple

---

#### 3. `app/ingestion/pdf_parser.py` — PDF text extraction

```python
def parse_pdf(content: bytes) -> str:
```

- Use `pdfplumber` — open from `io.BytesIO(content)`
- Extract text page by page; join with `\n`
- If a page returns no text (scanned), skip it silently
- Return the combined text string

---

#### 4. `app/ingestion/html_parser.py` — HTML text extraction

```python
def parse_html(content: bytes, url: str) -> str:
```

- Try `trafilatura.extract()` first — pass `url` as the `url` argument, `include_comments=False`, `include_tables=True`
- If `trafilatura` returns `None` or empty string, fall back to `BeautifulSoup(content, "html.parser").get_text(separator="\n")`
- Return the extracted text string

---

#### 5. `app/ingestion/normalizer.py` — text cleanup and hashing

```python
def normalize(text: str) -> str:
def compute_hash(text: str) -> str:
```

`normalize`:
- Strip leading/trailing whitespace
- Collapse runs of spaces/tabs to a single space (per line)
- Collapse 3+ consecutive blank lines to 2
- Return cleaned string

`compute_hash`:
- SHA-256 of `text.encode("utf-8")`
- Return hex digest string

---

#### 6. `app/ingestion/storage.py` — hash comparison, disk write, SQLite write

Three functions:

```python
def get_latest_hash(conn, doc_id: str) -> str | None:
def save_raw(source_id: str, file_format: str, content: bytes) -> Path:
def save_normalized(source_id: str, text: str) -> Path:
def write_version(conn, doc_id: str, version_data: dict) -> str:
```

- `get_latest_hash`: query `document_versions` for the most recent `content_hash` for this `doc_id`, ordered by `fetched_at DESC`. Return `None` if no prior version.
- `save_raw`: write `content` to `{settings.raw_data_dir}/{source_id}.{file_format}`. Create parent dirs. Return the path.
- `save_normalized`: write `text` to `{settings.normalized_data_dir}/{source_id}.txt`. Create parent dirs. Return the path.
- `write_version`: insert a row into `document_versions`. Generate `version_id` with `uuid.uuid4()`. Return the `version_id`.

---

#### 7. `app/ingestion/sync.py` — orchestrator

```python
def run_sync() -> dict:
```

Logic per source:
1. Load `sources/ph_law_sources.yaml`; skip entries where `enabled: false`
2. Upsert each source into the `documents` table (insert if new, update `updated_at` if exists)
3. Fetch via `fetcher.py`; if `status == "failed"`, record it and continue to next source
4. Parse content: use `pdf_parser` if `file_format == "pdf"`, `html_parser` if `html`
5. Normalize text via `normalizer.normalize()`
6. Compute hash via `normalizer.compute_hash()`
7. Compare against `storage.get_latest_hash()`:
   - If hash matches → mark as `unchanged`, skip disk writes and DB version insert
   - If different or no prior version → mark as `changed` or `new`
8. For `changed`/`new`: save raw, save normalized, call `storage.write_version()`
9. Track counts: `scanned`, `changed`, `unchanged`, `failed`
10. Write a row to `sync_runs` on completion

Return a summary dict:
```python
{"scanned": int, "changed": int, "unchanged": int, "failed": int}
```

Print per-source status as it runs: `[OK] civil_code — changed` / `[SKIP] civil_code — unchanged` / `[FAIL] civil_code — <error>`.

---

#### 8. Wire `raglab sync` in `app/cli/main.py`

Replace the stub:

```python
@app.command("sync")
def sync():
    from app.ingestion.sync import run_sync
    result = run_sync()
    typer.echo(f"\nSync complete: {result}")
```

Keep the import inside the function so the CLI doesn't fail at startup if ingestion deps aren't installed yet.

---

### Definition of done

- `raglab sync` fetches all enabled sources, prints per-source status, and writes a `sync_runs` row.
- Running sync a second time on an unchanged corpus prints `[SKIP]` for every source and writes no new `document_versions` rows.
- A changed document (edit the YAML to force a re-fetch or change a URL) produces a new `document_versions` row with `changed_from_previous = 1`.

### Key constraints

- Never raise inside `run_sync` — catch per-source errors and keep going.
- Hash normalized text, not raw bytes.
- `doc_id` = derived identifier — use `hashlib.sha256(url.encode()).hexdigest()[:16]` or just the `source_id` directly. Pick one and be consistent.
- No indexing logic here — `sync.py` only fetches, parses, normalizes, hashes, and versions. Indexing is Milestone 3.

---

## Milestone 3: Chunking, Embeddings, and Indexing

**Goal:** New and changed documents are chunked, embedded, and stored in Qdrant (dense) and BM25 (sparse). Re-running on unchanged docs skips indexing entirely.

### Prerequisites before writing any code

- Docker running Qdrant: `docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant`
- Ollama running with embed model pulled: `ollama pull nomic-embed-text`

### New config fields to add to `app/config.py`

```python
qdrant_collection: str = "ph_law"
qdrant_url: str = "http://localhost:6333"
bm25_path: str = "data/bm25"
chunk_size: int = 256
chunk_overlap: int = 32
embedding_model: str = "nomic-embed-text"
ollama_base_url: str = "http://localhost:11434"
```

### New packages

```
uv add llama-index-core llama-index-embeddings-ollama llama-index-retrievers-bm25 llama-index-vector-stores-qdrant qdrant-client
```

### New folder

```
app/indexing/__init__.py   (empty)
```

### What to build (in order)

---

#### 1. `app/indexing/chunker.py` — text chunker

```python
def chunk_text(text: str, source_metadata: dict) -> list[TextNode]:
```

- Use LlamaIndex `SentenceSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)`
- Call `splitter.get_nodes_from_documents([Document(text=text, metadata=source_metadata)])`
- Return the list of `TextNode` objects — each node carries the metadata automatically
- `source_metadata` should contain: `doc_id`, `source_id`, `title`, `url`, `doc_type`, `category`, `tags`

---

#### 2. `app/indexing/embedder.py` — Ollama embedding client

```python
def get_embed_model() -> OllamaEmbedding:
def embed_texts(texts: list[str]) -> list[list[float]]:
```

- Use `OllamaEmbedding(model_name=settings.embedding_model, base_url=settings.ollama_base_url)` from `llama_index.embeddings.ollama`
- `get_embed_model` returns the embedding model instance (call once, reuse)
- `embed_texts` calls `embed_model.get_text_embedding_batch(texts)` and returns the list of vectors

---

#### 3. `app/indexing/vector_store.py` — Qdrant wrapper

```python
def get_qdrant_client() -> QdrantClient:
def ensure_collection(client: QdrantClient) -> None:
def upsert_nodes(client: QdrantClient, nodes: list[TextNode], vectors: list[list[float]]) -> None:
def delete_by_doc_id(client: QdrantClient, doc_id: str) -> None:
```

- `get_qdrant_client`: returns `QdrantClient(url=settings.qdrant_url)`
- `ensure_collection`: creates the collection if it doesn't exist, with `VectorParams(size=768, distance=Distance.COSINE)`. Use `recreate_collection=False`.
- `upsert_nodes`: builds `PointStruct` objects from nodes + vectors, uses `chunk_id` from node metadata as the point ID, upserts in one batch call
- `delete_by_doc_id`: calls `client.delete(collection_name=..., points_selector=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]))` to remove all vectors for a document before re-indexing

---

#### 4. `app/indexing/bm25_store.py` — BM25 index

```python
def build_and_save(nodes: list[TextNode]) -> None:
def load() -> BM25Retriever | None:
```

- `build_and_save`: creates a `BM25Retriever.from_defaults(nodes=all_nodes, similarity_top_k=10)` from the full node list, then persists it to `settings.bm25_path` using `retriever.persist(path)`
- `load`: loads and returns the persisted retriever using `BM25Retriever.from_persist_dir(path)`, returns `None` if the path doesn't exist
- Note: BM25 must be rebuilt from **all** indexed nodes every time a document changes — it cannot be updated incrementally. Query `chunks` table for all existing chunks to reconstruct the full node list before rebuilding.

---

#### 5. `app/indexing/index_service.py` — indexing orchestrator

```python
def index_document(doc_id: str, text: str, source_metadata: dict, conn) -> int:
```

Logic:
1. Delete stale vectors from Qdrant: `delete_by_doc_id(client, doc_id)`
2. Delete stale rows from `chunks` table: `DELETE FROM chunks WHERE doc_id = ?`
3. Chunk the normalized text via `chunker.chunk_text()`
4. Embed all chunks in one batch via `embedder.embed_texts()`
5. Upsert vectors to Qdrant via `vector_store.upsert_nodes()`
6. Write each chunk to the `chunks` table in SQLite — include `chunk_id`, `doc_id`, `version_id`, `chunk_index`, `text`, `char_count`, `token_estimate`, `qdrant_id` (same as chunk_id), `metadata_json`, `created_at`
7. Rebuild BM25 index — load all chunks from `chunks` table, reconstruct nodes, call `bm25_store.build_and_save()`
8. Return the count of chunks indexed

---

#### 6. Wire indexing into `app/ingestion/sync.py`

In `process_source`, after a successful `insert_version` call, add:

```python
from app.indexing.index_service import index_document

chunk_count = index_document(doc_id, normalized_text, {
    "doc_id": doc_id,
    "source_id": source.source_id,
    "title": source.title,
    "url": source.url,
    "doc_type": source.doc_type,
    "category": source.category,
    "tags": source.tags,
}, conn)
print(f"         indexed {chunk_count} chunks")
```

Keep the import inside the function body so the CLI doesn't fail at startup if indexing deps are missing.

---

### Definition of done

- `raglab sync` automatically chunks and embeds new/changed documents after versioning them.
- Qdrant collection exists and holds vectors — verify at `http://localhost:6333/dashboard`.
- `data/bm25/` directory contains the persisted BM25 index files after first sync.
- Re-running sync on an unchanged corpus prints `[SKIP]` for all sources — no re-indexing happens.
- `chunks` table in SQLite has one row per chunk with correct `doc_id` and `qdrant_id`.

### Key constraints

- Embed in batch — do not call the embedding model once per chunk.
- BM25 is always rebuilt from scratch from the full `chunks` table — never try to update it incrementally.
- Delete stale vectors before re-indexing a changed document — never accumulate duplicate vectors.
- Keep all Qdrant and Ollama client instantiation inside functions, not at module level — avoids import-time failures if the services aren't running.

---

## Ongoing Review Instructions

When reviewing code:

1. Compare implementation against `docs/project_plan.md`.
2. Flag unnecessary complexity — LlamaIndex abstractions should simplify, not obscure.
3. Keep business logic out of Streamlit and FastAPI adapters.
4. Preserve incremental-sync architecture.
5. Preserve local-first design.
6. Prefer explicit retrieval trace in debug mode over silent failures.

If the implementation differs from the plan, note whether the difference is: acceptable simplification, technical debt, bug, scope creep, or worthwhile improvement.
