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

## Ongoing Review Instructions

When reviewing code:

1. Compare implementation against `docs/project_plan.md`.
2. Flag unnecessary complexity — LlamaIndex abstractions should simplify, not obscure.
3. Keep business logic out of Streamlit and FastAPI adapters.
4. Preserve incremental-sync architecture.
5. Preserve local-first design.
6. Prefer explicit retrieval trace in debug mode over silent failures.

If the implementation differs from the plan, note whether the difference is: acceptable simplification, technical debt, bug, scope creep, or worthwhile improvement.
