# Frontend tech specs (React + Vite workbench)

Per-phase, execution-fidelity specs for the React frontend described in
[`../project_plan.md`](../project_plan.md) (§ *Future Feature: React + Vite Frontend*).

**Purpose.** Each spec is a self-contained handoff artifact: an executor (Sonnet/Haiku
agent, or a human) should be able to implement the phase from the spec alone, with no
open design decisions. Architecture and rationale live in `project_plan.md`; these files
carry the *how* — exact files, commands, endpoint contracts, and checkable acceptance
criteria.

**Authoring model.** Specs are drafted with Opus. Execution is delegated to a cheaper
model (default: Sonnet; Haiku only for mechanical phases). Cheap execution only works when
the spec removes decisions — so every spec pins file paths, `response_model` field types,
exact commands, and acceptance checks that are machine-verifiable (`tsc --noEmit` clean,
`curl` returns shape X, tests green).

## Phases

| Phase | Spec | Scope | Status |
|-------|------|-------|--------|
| 0 | [phase-0-scaffold.md](phase-0-scaffold.md) | Vite+TS scaffold, Tailwind v4, shadcn, strict TS, OpenAPI codegen, test harnesses, first `response_model` | draft |
| 1 | [phase-1-corpus-browser.md](phase-1-corpus-browser.md) | Corpus browser: `GET /documents/{doc_id}` + chunks, YAML-enriched list; CorpusList + CorpusDetail | draft |
| 2 | [phase-2-chat.md](phase-2-chat.md) | Chat + inline `[n]` citations on `/query/ask` (typed `AskResponse`); read-only conversations (list + get) with sources persisted per turn | draft |
| 3 | [phase-3-dashboard.md](phase-3-dashboard.md) | Dashboard + Ingestion + Health; `/stats/overview`, `/sync/runs`, `/config`, typed `/health`; sync-lifecycle hardening (start-row + terminal status, `POST /documents/sync` returns `sync_run_id`) | draft |
| 4 | [phase-4-retrieval-lab.md](phase-4-retrieval-lab.md) | Retrieval Lab (`POST /retrieval/inspect` via in-process `run_answer` refactor) + Observability (`/traces`, `/traces/{id}`, `/logs`); one shared `TraceRecord` schema | draft |
| 5 | [phase-5-evaluations.md](phase-5-evaluations.md) | Evaluations (read-only): `/evals/runs`, `/evals/runs/{tag}`, `/evals/runs/{tag}/rows` (run⨝scored), `/evals/runs/{tag}/diff`; manifest-gated `eval_store.py`, metrics diff | draft |
| 6 | _deferred_ | Cost & Usage | deferred — no token/cost accounting exists in the pipeline (generators discard `resp.usage`; traces carry `prompt_length` chars, not tokens). Needs backend instrumentation first; revisit if cost tracking becomes a goal. |

## Invariants (apply to every phase)

- **Endpoints-first.** Add the route (with a Pydantic `response_model`) → `curl`-verify →
  `npm run gen:types` → build the frontend slice against generated types.
- **One schema, many consumers.** `response_model`s feed `/openapi.json`, which feeds the
  generated TS types, Swagger (`/docs`), ReDoc (`/redoc`), and Playwright mock fixtures.
  Never hand-declare API types in TS.
- **Strict TS everywhere**, including `src/components/ui/**` (shadcn) — patch inline.
- **Business logic stays in Python service modules;** FastAPI routes stay thin adapters.
- **Retrieval-touching verification pins `RERANKER_BACKEND=minilm`.** Bedrock is the config
  default and needs AWS creds; any guardrail/smoke/curl script that runs a real query through
  retrieval must set `RERANKER_BACKEND=minilm` (or note the creds dependency), or it fails on
  `NoCredentialsError` in a local/executor env rather than exercising the path. (Greeting/
  conversational questions short-circuit before retrieval and don't need this.)
