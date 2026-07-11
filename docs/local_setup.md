# Local Setup

Get from a fresh clone to a working, grounded answer in under 15 minutes.

The system is **local-first**: retrieval and generation run entirely on your
machine via Ollama and a local Qdrant. The only part that reaches the cloud is
the optional RAGAS eval scorer (see [Evals](#7-run-evals-optional)).

See also: [`architecture.md`](architecture.md) (how it works) ·
[`tradeoffs.md`](tradeoffs.md) (why these choices).

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| uv | latest | dependency manager |
| Docker | any | runs Qdrant; [Colima](https://github.com/abiosoft/colima) works without Docker Desktop |
| Ollama | latest | runs **natively on the host**, not in a container — see below |

> **Why Ollama is not containerized:** on Apple Silicon, only the host can use
> the GPU (Metal). A containerized Ollama runs in a Linux VM with no GPU access
> and is far slower. Qdrant, the API, and the UI are CPU/RAM only and stay in
> containers.

---

## 2. Install dependencies

```bash
git clone <your-repo-url> ph-law-rag
cd ph-law-rag
uv sync
```

---

## 3. Start the services

**Qdrant** (vector store):

```bash
docker compose up -d qdrant
```

**Ollama** must listen on all interfaces so the containers can reach it. The
default `127.0.0.1` binding rejects container traffic:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Pull the models (one time):

```bash
ollama pull gemma4:e4b         # generation
ollama pull nomic-embed-text   # embeddings (768-dim)
```

---

## 4. Configure

```bash
cp .env.example .env
```

Defaults work for a local (non-Docker) run. Two values differ when the API
runs **inside** docker-compose — these are set on the `api` service in
`docker-compose.yaml`, not in `.env`:

| Setting | Local run | Inside docker-compose |
|---|---|---|
| `qdrant_url` | `http://localhost:6333` | `http://qdrant:6333` |
| `ollama_base_url` | `http://localhost:11434` | `http://host.docker.internal:11434` |

---

## 5. Initialize and populate

```bash
raglab init     # creates data/ dirs and the SQLite DB
raglab sync     # fetches, normalizes, chunks, embeds, indexes the corpus
```

> **The database ships empty — you must run `raglab sync` before asking
> anything.** Without it, every query retrieves nothing and the system abstains.
> `sync` is incremental: re-running it skips unchanged documents.

Confirm everything is reachable:

```bash
raglab healthcheck
```

---

## 6. Use it

```bash
# Command line
raglab ask "What are the requisites of a valid contract under the Civil Code?"

# Web UI (React workbench) — run from the frontend/ dir
cd frontend && npm install --legacy-peer-deps && npm run dev   # http://localhost:5173
```

The Vite dev server proxies `/api` to the FastAPI backend on `:8000`, so start
`uvicorn app.api.main:app` (or the docker `api` service) alongside it.

---

## 7. Run evals (optional)

RAGAS scores answers with a cloud LLM (`ragas_llm_model`, default
`claude-sonnet-4-6`). This is the **only** part that is not local — set
`anthropic_api_key` in `.env` first.

```bash
raglab eval
```

---

## 8. Run the full stack in containers

```bash
docker compose up        # Qdrant + FastAPI (:8000) + React web UI (:8080)
```

With host Ollama running, the web UI (nginx serving the React SPA, reverse-
proxying `/api` to the API) answers an end-to-end query. The cross-encoder
reranker is pre-baked into the API image at build time, so the first query is
not blocked on a slow model download.

The data volume is shared from the host, so run `raglab sync` (locally or via
`docker compose exec api raglab sync`) before querying.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Query hangs for minutes on first ask | Reranker downloading from HuggingFace | Already pre-baked in the Docker image; for local runs the first ask downloads it once, then caches |
| Answers are always "I don't have enough information…" | Corpus not indexed | Run `raglab sync` |
| Container can't reach Ollama | Ollama bound to `127.0.0.1` | Restart with `OLLAMA_HOST=0.0.0.0:11434 ollama serve` |
| `healthcheck` shows `qdrant: false` | Qdrant not running | `docker compose up -d qdrant` |
| `raglab eval` fails on auth | Missing API key | Set `anthropic_api_key` in `.env` |
