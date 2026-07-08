# ADR-023: React + Vite frontend (retire Streamlit)

## Date

2026-07-08

## Status

Accepted

## Plain English

Replace the Streamlit UI with a decoupled React + Vite + TypeScript single-page app — a "legal RAG workbench" — served as a static build behind nginx in Docker. The frontend talks only to the typed FastAPI REST surface, with its TypeScript types generated from the OpenAPI schema so the API boundary cannot drift.

## Context

Through Milestone 5 the UI was a single Streamlit file (`app/ui/home.py`): a chat tab (POST `/query/ask`) plus a sources tab (`GET /documents`) and a debug sidebar. That was enough to prove the pipeline but not to surface the parts of this system that are actually differentiating — corpus browsing with amendment edges, retrieval traces, eval quality and run-to-run diffs, sync/ingestion lifecycle, and (eventually) cost. Streamlit's server-rendered, Python-coupled model made those hard to build well, and the project needed a real React/TypeScript surface as a portfolio signal.

The backend already had the right shape for a decoupled client: thin FastAPI adapters over Python service modules. The missing piece was a typed read/serving surface and a frontend that consumes it without hand-written API models.

## Decision

Adopt a decoupled SPA:

- **Stack:** Vite + React + TypeScript (strict, plus `noUncheckedIndexedAccess` / `noImplicitReturns` / `exactOptionalPropertyTypes`), React Router, TanStack Query + Table, Tailwind v4 + shadcn/ui. Code lives in `frontend/`, a sibling to `app/`.
- **Typed API boundary:** every FastAPI route ships a Pydantic `response_model`; `openapi-typescript` generates `frontend/src/api/schema.ts` from `/openapi.json`; the client derives all request/response types from the generated `paths` — no hand-written API types. One schema feeds the TS types, Swagger, ReDoc, and Playwright mock fixtures.
- **Serving:** in dev, the Vite proxy maps `/api` → `http://localhost:8000` (no CORS middleware). In Docker (local compose and AWS), a `web` container built from `frontend/Dockerfile` (multi-stage node build → `nginx:alpine`) serves the static SPA and reverse-proxies `/api` → `api:8000` (`frontend/nginx.conf`), so the browser only ever talks to nginx and the API stays internal.
- **Execution model:** the frontend is built as a phased program with execution-fidelity specs in `docs/frontend/` (one file per phase). Opus drafts specs; a cheaper model executes each in a fresh session. Phases 1–5 (corpus, chat + citations + conversations, dashboard/ingestion/health, retrieval lab + observability, evaluations) are shipped; Phase 6 (cost & usage) is deferred pending token accounting.
- **Retire Streamlit on parity:** once React chat + corpus workflows were stable, remove `app/ui/`, the `streamlit` dependency, and the Streamlit-only `api_base_url` / `api_request_timeout` settings; repoint the compose and CDK UI service from Streamlit (`:8501`) to the React nginx build (`:80`).

## Alternatives Considered

1. **Keep / extend Streamlit.** Fastest and Python-native, but a poor fit for multi-page inspection UIs (retrieval traces, eval diffs, corpus detail) and gives no React/TypeScript portfolio signal. Rejected.
2. **Next.js (SSR/RSC).** More framework than a local, single-user developer workbench needs; SSR adds a Node server to run and deploy for no benefit here. Rejected in favor of a static SPA.
3. **Plain Vite SPA with hand-written API types.** Same UI, but hand-declared TS request/response models drift from the backend. Rejected in favor of OpenAPI-generated types as a hard rule.
4. **Serve the SPA directly with CORS on FastAPI (no nginx proxy).** Would expose the API publicly and require CORS config and a baked-in API URL. Rejected: the nginx `/api` reverse-proxy keeps the API internal (same security posture as the old Streamlit topology) with no CORS.

## Reasons

- The differentiating surfaces (traces, eval diffs, corpus/amendment browsing) need real client-side UI that Streamlit does not do well.
- Generating types from one OpenAPI schema makes the API boundary drift-proof and is itself a quality signal.
- The nginx reverse-proxy mirrors the Vite dev proxy exactly, so the client's `/api` calls work identically in dev, local Docker, and AWS — with no CORS and the API never public.
- A decoupled SPA over unchanged Python service modules is a clean adapter swap: business logic stays in `app/`, the frontend is purely a consumer.
- Phased, spec-driven delegation kept execution cheap while planning/review stayed on Opus.

## Consequences

- The Docker topology gains a `web` (nginx) container reverse-proxying `/api`; the old "one image, two entrypoints" model is replaced by two images — the Python API (from ECR) and the React/nginx web image (built by CDK `ContainerImage.from_asset("frontend")`, ARM64).
- CDK migrated: the UI Fargate service, security group, and ALB target moved from Streamlit `:8501` (health `/_stcore/health`) to nginx `:80` (health `/healthz`). Validated by `cdk synth`; a real deploy still exercises the nginx→Service Connect DNS path.
- `app/ui/` and the `streamlit` dependency are removed; `api_base_url` / `api_request_timeout` dropped from config and `.env.example`.
- Every new serving/read endpoint must carry a `response_model` (the codegen gate); routes returning bare dicts emit as untyped `object`.
- Retrieval-touching verification scripts must pin `RERANKER_BACKEND=minilm` (Bedrock is the default and needs AWS creds) — captured as an invariant in `docs/frontend/README.md`.
- **Amends [ADR-020](020-cloud-runtime-profile.md):** the cloud UI entrypoint is now React/nginx, not Streamlit. ADR-020's broader cloud runtime profile (Bedrock embeddings, Anthropic generation, Qdrant Cloud, MiniLM serving reranker, ALB-fronts-UI-only + internal API via Service Connect) is unchanged and still stands.
