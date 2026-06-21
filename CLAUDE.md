# ph-law-rag — Claude Instructions

`docs/project_plan.md` is the source of truth for architecture, data model, config, and stack. Read it before building. Update it when implementation meaningfully diverges. Don't restate plan content here — reference it.

Devlog: `/Users/jeromeagapay/Documents/Personal/muming/03_Outputs/ph-law-rag-devlog.md`. Append or update entries there for meaningful implementation decisions, bugs, fixes, eval results, and deployment-gate findings.

## Response style

- Be terse. No preamble, no postamble, no pleasantries.
- State the problem and fix directly. No "Great question!" or "Let me help you with that."
- No casual filler words. Skip recaps of what I asked.
- Code/commands first, minimal prose.

## Working rules

- Be concise. Implementation first, minimal explanation unless asked. Plain language over jargon.
- Use `uv` for deps (`uv add <pkg>`). Python 3.11+.
- Lazy/in-function imports for optional deps (Qdrant, Ollama, ingestion, indexing) so the CLI never fails at startup when a service or package is missing.
- Adapters (`app/api`, `app/ui`) hold no business logic.
- Config field names/defaults come from the plan's Config System section — match exactly.
- `embedding_backend` is the source of truth for embedding defaults. Do not require separate model/dim exports for the standard Ollama or Bedrock paths; `Settings` derives and validates them.
- Work the current milestone only; don't pull work forward.
- The user codes the milestones themselves. Default to showing code or pseudocode in the chat — do NOT write, create, or edit files unless the user explicitly says to (e.g. "write it", "create the file", "update X"). "Give me the code" / "show me" means display it, not apply it.
- Don't assume. When intent, scope, or approach is ambiguous, ask first rather than acting — unless the user has explicitly told you to proceed.

---

## Milestone 1 — Scaffold & local runtime

**Goal:** project boots cleanly, all entry points work.

Build:

- Repo folder structure per plan's Architecture section (`__init__.py` where needed).
- `pyproject.toml`: `raglab` console script, all deps from plan's stack up front.
- `app/config.py`: `Settings` via `pydantic-settings`, loaded from `.env`.
- `.env.example`: one line per key, default + short comment.
- `app/db.py`: connect to `settings.db_path` (create file + parent dirs); `schema_migrations` table; apply migrations in order, skipping applied ones. Migration 1 creates `documents`, `document_versions`, `chunks`, `sync_runs` per plan's Data Model.
- CLI (Typer, registered as `raglab`). All commands stubs except: `init` (creates the `data/*` dirs, bootstraps DB via `db.py`, prints confirmation) and `show-config` (prints `settings.model_dump()` as JSON). Stubs: `sync`, `ask`, `eval`, `healthcheck`.
- `app/api/main.py`: FastAPI, `GET /health` → `{"status": "ok"}`.
- `app/ui/app.py`: Streamlit stub, title "PH Law RAG", placeholder text.

**Done when:** `raglab init` runs error-free and is idempotent on repeat; `raglab show-config` prints config JSON; `uvicorn app.api.main:app` serves `/health`; `streamlit run app/ui/app.py` opens without error; config loads from `.env`.

**Don't:** start ingestion/retrieval/indexing.

---

## Milestone 2 — Document sync & normalization

**Goal:** `raglab sync` fetches, normalizes, hashes, versions docs. Unchanged corpus → all processing skipped.

Add config: `raw_data_dir`, `normalized_data_dir`, `source_config_path`, `request_timeout`.
Add deps: `httpx pdfplumber trafilatura beautifulsoup4 pyyaml`.
New pkg: `app/ingestion/`.

Build:

1. `sources/ph_law_sources.yaml` — expand to ~25–30 enabled sources covering: 1987 Constitution; Civil Code (RA 386), Family Code (EO 209); Revised Penal Code (Act 3815); RA 9262 / 10175 / 10173 / 8293; 5–10 SC E-Library decisions. Fix typo `consitution_1987` → `constitution_1987`. Each entry: `source_id, title, url, doc_type, file_format (html|pdf), category, tags[], enabled`.
2. `fetcher.py` — `httpx` GET with `User-Agent` + `timeout=settings.request_timeout`. Returns a `FetchResult` dataclass (`source_id, url, file_format, status[ok|failed], http_status, content, error`). Never raises; failures set `status="failed"` + `error`. No retries.
3. `pdf_parser.py` — `parse_pdf(content: bytes) -> str`: `pdfplumber` over `BytesIO`, text per page joined with `\n`, skip empty (scanned) pages.
4. `html_parser.py` — `parse_html(content: bytes, url: str) -> str`: `trafilatura.extract(url=url, include_comments=False, include_tables=True)`; on None/empty fall back to `BeautifulSoup(...).get_text("\n")`.
5. `normalizer.py` — `normalize(text)`: strip, collapse intra-line whitespace, collapse 3+ blank lines to 2. `compute_hash(text)`: SHA-256 hex of UTF-8.
6. `storage.py` — `get_latest_hash(conn, doc_id)` (latest `content_hash` by `fetched_at DESC`, else None); `save_raw` → `{raw_data_dir}/{source_id}.{fmt}`; `save_normalized` → `{normalized_data_dir}/{source_id}.txt` (both create parent dirs, return path); `write_version(conn, doc_id, data)` inserts into `document_versions`, `version_id = uuid4()`, returns it.
7. `sync.py` — `run_sync() -> dict`. Per enabled source: upsert into `documents`; fetch (on fail, record + continue); parse by format; normalize; hash; compare to latest. Matching hash → `unchanged`, skip disk + version insert. New/different → save raw + normalized + `write_version`. Track `scanned/changed/unchanged/failed`; write a `sync_runs` row at end; return those counts. Print per-source: `[OK] <id> — changed` / `[SKIP] <id> — unchanged` / `[FAIL] <id> — <error>`.
8. Wire `raglab sync` to call `run_sync()` (import inside the command).

**Done when:** sync fetches all enabled sources, prints per-source status, writes a `sync_runs` row; second run on unchanged corpus prints `[SKIP]` for all and writes no new versions; a changed doc yields a new `document_versions` row with `changed_from_previous = 1`.

**Constraints:** never raise inside `run_sync`. Hash normalized text, not raw bytes. Pick one `doc_id` scheme and stay consistent. No indexing here.

---

## Milestone 3 — Chunking, embeddings, indexing

**Goal:** new/changed docs are chunked, embedded, stored in Qdrant (dense) + BM25 (sparse). Unchanged docs → no re-indexing.

Prereqs: Qdrant (`docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant`), Ollama (`ollama pull nomic-embed-text`).
Add config: `qdrant_collection, qdrant_url, bm25_path, chunk_size=256, chunk_overlap=32, embedding_model, ollama_base_url`.
Add deps: `llama-index-core llama-index-embeddings-ollama llama-index-retrievers-bm25 llama-index-vector-stores-qdrant qdrant-client`.
New pkg: `app/indexing/`.

Build:

1. `chunker.py` — `chunk_text(text, source_metadata) -> list[TextNode]` via LlamaIndex `SentenceSplitter(chunk_size, chunk_overlap)` over a single `Document`. Metadata carries `doc_id, source_id, title, url, doc_type, category, tags`.
2. `embedder.py` — `get_embed_model()` returns a reusable `OllamaEmbedding(model_name, base_url)`; `embed_texts(texts)` → `get_text_embedding_batch(texts)`.
3. `vector_store.py` — `get_qdrant_client()` (`QdrantClient(url)`); `ensure_collection` (create if absent, `VectorParams(size=768, distance=COSINE)`, no recreate); `upsert_nodes` (one batched upsert, point ID = node `chunk_id`); `delete_by_doc_id` (filter on `doc_id`, delete before re-index).
4. `bm25_store.py` — `build_and_save(nodes)` builds `BM25Retriever.from_defaults(similarity_top_k=10)` and persists to `bm25_path`; `load()` returns persisted retriever or None. BM25 has no incremental update — always rebuild from the full node set (reconstruct from `chunks` table).
5. `index_service.py` — `index_document(doc_id, text, source_metadata, conn) -> int`: delete stale Qdrant vectors (`delete_by_doc_id`) and `chunks` rows for `doc_id`; chunk; batch-embed; upsert vectors; write each chunk to `chunks` (`chunk_id, doc_id, version_id, chunk_index, text, char_count, token_estimate, qdrant_id=chunk_id, metadata_json, created_at`); rebuild BM25 from all chunks; return chunk count.
6. Wire into `sync.py`: after a successful version insert, call `index_document(...)` with the source metadata and print indexed chunk count (import inside the function).

**Done when:** sync auto-chunks/embeds new/changed docs after versioning; Qdrant holds vectors (check `:6333/dashboard`); `data/bm25/` has the persisted index; unchanged corpus → `[SKIP]` all, no re-index; `chunks` has one row per chunk with correct `doc_id`/`qdrant_id`.

**Constraints:** embed in one batch, never per-chunk. Always rebuild BM25 from scratch off the full `chunks` table. Delete stale vectors before re-indexing — no duplicates. Instantiate Qdrant/Ollama clients inside functions, never at module level.

---

## Review checklist

Compare against `docs/project_plan.md`. Flag unnecessary complexity (LlamaIndex should simplify, not obscure). Keep business logic out of Streamlit/FastAPI. Preserve incremental-sync and local-first design. Prefer an explicit retrieval trace in debug mode over silent failure.

For any divergence from the plan, label it: acceptable simplification / tech debt / bug / scope creep / improvement.
