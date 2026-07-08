# Phase 0 — Frontend scaffold & typed API boundary

**Goal.** Stand up a `frontend/` React+TS SPA that renders a shell, talks to the existing
FastAPI over a dev proxy, and consumes **generated** API types. Establish the strict-TS,
OpenAPI-codegen, and test harnesses that every later phase inherits. No product features.

**Executor guidance.** Mechanical + config-heavy → suitable for a cheaper model, but the
strict-TS and codegen steps are exact; follow them verbatim. Do not add pages, routes, or
endpoints beyond what's listed. Stop at the acceptance checks.

**Preconditions.**
- Backend runs locally: `uvicorn app.api.main:app --reload` serves on `:8000`.
- Node ≥ 20, npm ≥ 10.
- Working dir for all `npm` commands is `frontend/` unless stated.

---

## Part A — Backend: type the existing `GET /documents`

The codegen is only useful once at least one route has a `response_model`. Retrofit
`GET /documents` and give the app a title/version for clean Swagger.

### A1. Add `FastAPI` metadata — `app/api/main.py`

Change `app = FastAPI()` to:

```python
app = FastAPI(title="PH Law RAG API", version="0.1.0")
```

(Leave everything else in `main.py` as-is.)

### A2. Response models — `app/api/routes_documents.py`

`db.list_documents()` returns rows with exactly these keys:
`doc_id, source_id, title, url, doc_type, category, enabled, updated_at, last_fetched, chunk_count`.
`enabled` is stored as SQLite int (0/1); `last_fetched` and `updated_at` may be `NULL`.

Add models and wire `response_model`. Keep the `{"documents": [...]}` envelope (stable API):

```python
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from app.sync_service import run_sync
from app.db import list_documents

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentSummary(BaseModel):
    doc_id: str
    source_id: str
    title: str
    url: str
    doc_type: str
    category: str
    enabled: bool
    updated_at: str | None = None
    last_fetched: str | None = None
    chunk_count: int


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class SyncStartedResponse(BaseModel):
    status: str


@router.get("", response_model=DocumentListResponse, summary="List all documents")
def documents() -> DocumentListResponse:
    return DocumentListResponse(documents=list_documents())


@router.post("/sync", response_model=SyncStartedResponse, summary="Trigger a background sync")
def sync(background_tasks: BackgroundTasks) -> SyncStartedResponse:
    background_tasks.add_task(run_sync)
    return SyncStartedResponse(status="sync started")
```

Pydantic coerces `enabled` 0/1 → bool automatically. If a strict-mode coercion error
appears, add `model_config = ConfigDict(coerce_numbers_to_str=False)` is **not** needed;
bool-from-int coercion is on by default — leave as written.

### A3. Verify backend

```bash
uvicorn app.api.main:app --reload         # in repo root, separate shell
curl -s localhost:8000/documents | head -c 400
curl -s localhost:8000/openapi.json | python -m json.tool | grep -A2 DocumentSummary
```

**Backend acceptance:** `GET /documents` returns `{"documents":[...]}`; `/openapi.json`
contains `DocumentSummary` and `DocumentListResponse` schemas; `/docs` renders the two
documents routes with models.

---

## Part B — Scaffold the frontend

### B1. Create the app

```bash
# repo root
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

### B2. Dependencies

```bash
npm i react-router-dom @tanstack/react-query @tanstack/react-table
npm i tailwindcss @tailwindcss/vite
npm i -D openapi-typescript @types/node
npm i -D vitest @testing-library/react @testing-library/jest-dom jsdom
npm i -D @playwright/test
npx playwright install --with-deps chromium
```

### B3. Tailwind v4 (CSS-first, no config file)

`src/index.css` — replace entire contents with:

```css
@import "tailwindcss";
```

### B4. Path aliases — **before** shadcn init

`vite.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
```

> Proxy note: frontend calls `/api/documents`; the rewrite strips `/api` so the backend
> (which has no `/api` prefix) receives `/documents`. Keep all client calls under `/api/*`.

`tsconfig.app.json` — add to `compilerOptions` (alongside the template's `strict: true`):

```jsonc
"baseUrl": ".",
"paths": { "@/*": ["./src/*"] },

"noUncheckedIndexedAccess": true,
"noImplicitReturns": true,
"exactOptionalPropertyTypes": true
```

Also add `baseUrl`/`paths` to the root `tsconfig.json` `compilerOptions` if the editor
needs it for resolution (the template splits config into `tsconfig.app.json` +
`tsconfig.node.json`).

### B5. shadcn init + components

```bash
npx shadcn@latest init          # accept defaults; it detects Tailwind v4 + aliases
npx shadcn@latest add table button badge input select scroll-area
```

If any file under `src/components/ui/` fails `tsc` under `exactOptionalPropertyTypes`,
**fix it inline** (typically: mark a prop `foo?: X | undefined` or narrow a spread). Do not
relax the tsconfig.

---

## Part C — API client, codegen, app shell

### C1. Codegen script — `package.json` `scripts`

```jsonc
"gen:types": "openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts",
"typecheck": "tsc --noEmit",
"test:unit": "vitest run",
"test:e2e": "playwright test"
```

Run it (backend must be up):

```bash
npm run gen:types
```

Commit the generated `src/api/schema.ts`.

### C2. Typed client — `src/api/client.ts`

```ts
import type { paths } from "@/api/schema";

type DocumentListResponse =
  paths["/documents"]["get"]["responses"]["200"]["content"]["application/json"];

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export function listDocuments(): Promise<DocumentListResponse> {
  return apiGet<DocumentListResponse>("/documents");
}
```

### C3. Query provider + router — `src/main.tsx`

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
```

### C4. Shell that renders live data — `src/App.tsx`

Minimal shell proving the full path (proxy → typed client → TanStack Query → render).
No styling ambitions; a heading + count is enough for Phase 0.

```tsx
import { useQuery } from "@tanstack/react-query";
import { listDocuments } from "@/api/client";

export default function App() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
  });

  return (
    <main className="p-8">
      <h1 className="text-2xl font-semibold">PH Law RAG — Workbench</h1>
      {isLoading && <p>Loading…</p>}
      {error && <p className="text-red-600">Failed to load documents.</p>}
      {data && <p>{data.documents.length} documents in corpus.</p>}
    </main>
  );
}
```

---

## Part D — Test harnesses (wired, minimal tests)

Phase 0 only proves the harnesses run; real coverage arrives with features.

### D1. Vitest — `vitest.config.ts`

```ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: { environment: "jsdom", globals: true, setupFiles: "./src/test/setup.ts" },
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
});
```

`src/test/setup.ts`:

```ts
import "@testing-library/jest-dom";
```

`src/api/client.test.ts` (proves client + typing under test):

```ts
import { expect, test, vi } from "vitest";
import { listDocuments } from "@/api/client";

test("listDocuments hits /api/documents and returns typed shape", async () => {
  const payload = { documents: [] };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })));
  const result = await listDocuments();
  expect(result.documents).toEqual([]);
});
```

### D2. Playwright — hybrid lanes — `playwright.config.ts`

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
  use: { baseURL: "http://localhost:5173" },
  projects: [
    { name: "mocked", testMatch: /.*\.mocked\.spec\.ts/ },   // default CI lane, /api/* intercepted
    { name: "smoke", testMatch: /.*\.smoke\.spec\.ts/ },      // real backend, run manually/nightly
  ],
});
```

`e2e/shell.mocked.spec.ts` (default lane — no backend needed):

```ts
import { test, expect } from "@playwright/test";

test("shell renders corpus count from mocked API", async ({ page }) => {
  await page.route("**/api/documents", (route) =>
    route.fulfill({ json: { documents: [{ doc_id: "x" }] } }),
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Workbench/ })).toBeVisible();
  await expect(page.getByText(/1 documents in corpus/)).toBeVisible();
});
```

> Mock fixtures should mirror the generated schema shape. As the API grows, derive fixtures
> from `schema.ts` types so mocks can't drift from the contract.

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `GET /documents` returns `{"documents":[...]}`; `/openapi.json` exposes
      `DocumentSummary` + `DocumentListResponse`; `/docs` (Swagger) and `/redoc` render them.

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` regenerates `src/api/schema.ts` from the live API with no error.
- [ ] `npm run typecheck` (`tsc --noEmit`) is **clean** under strict + `noUncheckedIndexedAccess`
      + `noImplicitReturns` + `exactOptionalPropertyTypes`, including `src/components/ui/**`.
- [ ] `npm run dev` + backend up → `http://localhost:5173` shows the heading and a real
      "N documents in corpus." count fetched through the `/api` proxy.
- [ ] `npm run test:unit` passes (`client.test.ts`).
- [ ] `npx playwright test --project=mocked` passes with **no backend running**.
- [ ] `src/api/client.ts` imports types from `@/api/schema` — **no hand-written** request/
      response interfaces anywhere.
- [ ] Path alias `@/*` resolves in Vite, `tsc`, and Vitest.

## Out of scope (do NOT build here)

- Any page beyond the Phase 0 shell (Corpus/Chat/Dashboard/etc.).
- New backend endpoints beyond typing the existing `GET /documents` + `POST /documents/sync`.
- CORS middleware (dev uses the Vite proxy; prod is same-origin).
- Styling/layout system, nav chrome, dark mode.
- Retiring or touching the Streamlit app.

## Handoff notes for the executor

- If `exactOptionalPropertyTypes` produces errors in shadcn `ui/` files, fix them inline;
  never loosen tsconfig or add `// @ts-ignore`.
- `gen:types` and the Playwright `smoke` lane need the backend on `:8000`; the `mocked`
  lane and `test:unit` must not.
- Report back: the final `tsc --noEmit` output, the `documents` count shown in the browser,
  and the three test-command results.
