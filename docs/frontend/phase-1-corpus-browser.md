# Phase 1 — Corpus browser (list + filters + detail + source metadata)

**Goal.** Turn the Phase 0 shell into a working corpus browser: a filterable document
table and a detail view that shows source metadata, the normalized legal text, and the
document's chunks. Three new/typed endpoints feed generated TS types; two routed pages
consume them.

**Executor guidance.** Backend code below is exact — type it verbatim (field names/types
are the codegen contract). Frontend component code is pinned for the client, routing, and
column/filter contracts; fill idiomatic TanStack Table/JSX around the given shapes. Do not
add pages, endpoints, or shadcn components beyond those listed. Stop at the acceptance
checks. Keep business logic in Python service modules — routes stay thin adapters.

**Preconditions.**
- Phase 0 complete and green (scaffold, strict TS, codegen, test harnesses).
- Backend runs locally: `uvicorn app.api.main:app --reload` on `:8000` against a synced DB
  (`data/*` populated; `raglab sync` has run so `documents`/`chunks` are non-empty).
- Working dir for all `npm` commands is `frontend/`.
- shadcn components already present from Phase 0: `table button badge input select
  scroll-area`. **Do not add more** — build the detail/collapsible UI with divs + Tailwind.

---

## Part A — Backend: readers, enrichment service, three typed routes

Filter fields (`status`, `source_index`, `official_number`) and the amendment edges live
only in `sources/ph_law_sources.yaml` (`SourceConfig`), not in the `documents` table. Phase
1 merges the YAML into the DB rows in a small service module; SQLite readers stay in
`db.py`; routes stay thin.

### A1. DB readers — `app/db.py`

Add two readers (leave `list_documents()` unchanged). Match the existing tab-indented style.

```python
def get_document(doc_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
                SELECT
                    d.doc_id, d.source_id, d.title, d.url, d.doc_type, d.category,
                    d.enabled, d.updated_at,
                    v.fetched_at AS last_fetched, v.content_hash, v.content_length,
                    v.extraction_method, v.http_status, v.normalized_path,
                    (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.doc_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_versions v ON v.doc_id = d.doc_id
                WHERE d.doc_id = ?
                ORDER BY v.fetched_at DESC
                LIMIT 1
            """,
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_chunks(doc_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
                SELECT chunk_id, chunk_index, text, char_count, token_estimate, qdrant_id
                FROM chunks
                WHERE doc_id = ?
                ORDER BY chunk_index
            """,
            (doc_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

> `get_document` returns the **latest** version row (by `fetched_at DESC`). `normalized_path`
> is read server-side to load the text and is **not** exposed in the API response (no
> absolute server paths on the wire).

### A2. Enrichment service — new file `app/corpus_service.py`

Merges YAML `SourceConfig` into DB rows and reads normalized text. Keyed by `source_id`.
Loads the **full** source map (including `enabled: false` sources) directly from YAML — do
**not** reuse `load_allowed_sources()` (it filters to enabled only).

```python
from pathlib import Path

import yaml

from app.config import SourceConfig, SourceFile, settings
from app.db import get_document, list_documents


def _source_map() -> dict[str, SourceConfig]:
    path = Path(settings.source_config_path)
    data = yaml.safe_load(path.read_text()) or {}
    parsed = SourceFile.model_validate(data)
    return {s.source_id: s for s in parsed.sources}


def list_documents_enriched() -> list[dict]:
    smap = _source_map()
    out: list[dict] = []
    for row in list_documents():
        src = smap.get(row["source_id"])
        out.append(
            {
                **row,
                "status": src.status if src else "unknown",
                "source_index": src.source_index if src else None,
                "official_number": src.official_number if src else None,
                "tags": src.tags if src else [],
            }
        )
    return out


def get_document_detail(doc_id: str) -> dict | None:
    row = get_document(doc_id)
    if row is None:
        return None

    normalized_path = row.pop("normalized_path", None)
    text = ""
    if normalized_path and Path(normalized_path).exists():
        text = Path(normalized_path).read_text(encoding="utf-8")

    src = _source_map().get(row["source_id"])
    return {
        **row,
        "normalized_text": text,
        "status": src.status if src else "unknown",
        "source_index": src.source_index if src else None,
        "official_number": src.official_number if src else None,
        "tags": src.tags if src else [],
        "approval_date": src.approval_date.isoformat() if src and src.approval_date else None,
        "effectivity_date": (
            src.effectivity_date.isoformat() if src and src.effectivity_date else None
        ),
        "availability": src.availability if src else None,
        "structure": src.structure if src else None,
        "notes": src.notes if src else None,
        "amends": src.amends if src else [],
        "repeals": src.repeals if src else [],
        "supersedes": src.supersedes if src else [],
        "implements": src.implements if src else [],
        "amends_namespace": src.amends_namespace if src else None,
    }
```

### A3. Response models + routes — `app/api/routes_documents.py`

Extend `DocumentSummary` with the four enrichment fields, add `DocumentDetail`,
`ChunkSummary`, `ChunkListResponse`, and wire the two new GETs. Keep the `POST /documents/sync`
route from Phase 0 unchanged. Full file:

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.corpus_service import get_document_detail, list_documents_enriched
from app.db import list_chunks
from app.sync_service import run_sync

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
    status: str = "unknown"
    source_index: str | None = None
    official_number: str | None = None
    tags: list[str] = []


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class DocumentDetail(DocumentSummary):
    normalized_text: str
    content_hash: str | None = None
    content_length: int | None = None
    extraction_method: str | None = None
    http_status: int | None = None
    approval_date: str | None = None
    effectivity_date: str | None = None
    availability: str | None = None
    structure: str | None = None
    notes: str | None = None
    amends: list[str] = []
    repeals: list[str] = []
    supersedes: list[str] = []
    implements: list[str] = []
    amends_namespace: str | None = None


class ChunkSummary(BaseModel):
    chunk_id: str
    chunk_index: int | None = None
    text: str
    char_count: int
    token_estimate: int
    qdrant_id: str | None = None


class ChunkListResponse(BaseModel):
    doc_id: str
    chunk_count: int
    chunks: list[ChunkSummary]


class SyncStartedResponse(BaseModel):
    status: str


@router.get("", response_model=DocumentListResponse, summary="List all documents")
def documents() -> DocumentListResponse:
    return DocumentListResponse(documents=list_documents_enriched())


@router.get(
    "/{doc_id}",
    response_model=DocumentDetail,
    summary="Document metadata + normalized text",
)
def document_detail(doc_id: str) -> DocumentDetail:
    detail = get_document_detail(doc_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentDetail(**detail)


@router.get(
    "/{doc_id}/chunks",
    response_model=ChunkListResponse,
    summary="Chunks for a document",
)
def document_chunks(doc_id: str) -> ChunkListResponse:
    if get_document_detail(doc_id) is None:
        raise HTTPException(status_code=404, detail="document not found")
    chunks = list_chunks(doc_id)
    return ChunkListResponse(doc_id=doc_id, chunk_count=len(chunks), chunks=chunks)
```

> `DocumentDetail` extends `DocumentSummary`, so it inherits every summary field. The
> `**detail` / `**chunk` spreads rely on the reader dicts carrying exactly the model's
> field names — do not rename keys in the service.
> `enabled` (SQLite int 0/1) coerces to `bool` automatically, as in Phase 0.

### A4. Verify backend

```bash
uvicorn app.api.main:app --reload            # repo root, separate shell
# grab a real doc_id from the list, then hit detail + chunks
DID=$(curl -s localhost:8000/documents | python -c "import sys,json;print(json.load(sys.stdin)['documents'][0]['doc_id'])")
curl -s "localhost:8000/documents" | python -m json.tool | grep -m1 -E '"status"|"source_index"'
curl -s "localhost:8000/documents/$DID" | python -m json.tool | grep -E '"normalized_text"|"amends"|"status"' | head
curl -s "localhost:8000/documents/$DID/chunks" | python -c "import sys,json;d=json.load(sys.stdin);print('chunks',d['chunk_count'])"
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/documents/__nope__     # expect 404
curl -s localhost:8000/openapi.json | python -m json.tool | grep -E 'DocumentDetail|ChunkSummary|ChunkListResponse'
```

**Backend acceptance:** list rows carry `status`/`source_index`/`official_number`/`tags`;
`GET /documents/{id}` returns `normalized_text` + amendment edges + version fields; a bad id
→ 404; `GET /documents/{id}/chunks` returns `{doc_id, chunk_count, chunks[]}`; `/openapi.json`
exposes `DocumentDetail`, `ChunkSummary`, `ChunkListResponse`; `/docs` renders all three
documents GETs with models.

---

## Part B — Frontend: regen types + typed client

### B1. Regenerate types (backend must be up)

```bash
npm run gen:types        # rewrites src/api/schema.ts
```

Commit the regenerated `src/api/schema.ts`.

### B2. Client — extend `src/api/client.ts`

Keep the existing `apiGet` + `listDocuments`. Add exported row/detail/chunk types and two
getters. **No hand-written response shapes** — derive everything from `paths`.

```ts
import type { paths } from "@/api/schema";

type DocumentListResponse =
  paths["/documents"]["get"]["responses"]["200"]["content"]["application/json"];
type DocumentDetail =
  paths["/documents/{doc_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type ChunkListResponse =
  paths["/documents/{doc_id}/chunks"]["get"]["responses"]["200"]["content"]["application/json"];

export type DocumentSummary = DocumentListResponse["documents"][number];

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export function listDocuments(): Promise<DocumentListResponse> {
  return apiGet<DocumentListResponse>("/documents");
}

export function getDocument(docId: string): Promise<DocumentDetail> {
  return apiGet<DocumentDetail>(`/documents/${encodeURIComponent(docId)}`);
}

export function listChunks(docId: string): Promise<ChunkListResponse> {
  return apiGet<ChunkListResponse>(`/documents/${encodeURIComponent(docId)}/chunks`);
}
```

---

## Part C — Frontend: routing, layout, pages

### C1. Layout + router — `src/App.tsx`

Phase 0's `App.tsx` was a single shell. Replace it with a minimal layout + routes.
`BrowserRouter` is already provided in `main.tsx` (leave `main.tsx` unchanged).

Routes:
- `/` → `CorpusList`
- `/documents/:docId` → `CorpusDetail`

```tsx
import { Link, Route, Routes } from "react-router-dom";
import CorpusList from "@/routes/CorpusList";
import CorpusDetail from "@/routes/CorpusDetail";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b px-6 py-3">
        <Link to="/" className="text-lg font-semibold">
          PH Law RAG — Workbench
        </Link>
      </header>
      <main className="p-6">
        <Routes>
          <Route path="/" element={<CorpusList />} />
          <Route path="/documents/:docId" element={<CorpusDetail />} />
        </Routes>
      </main>
    </div>
  );
}
```

### C2. Corpus list — `src/routes/CorpusList.tsx`

A filterable table over `listDocuments()`. Use TanStack Table with shadcn `table`; use
shadcn `input`, `select`, `badge`, `button`.

**Data:** `useQuery({ queryKey: ["documents"], queryFn: listDocuments })`.

**Columns** (`ColumnDef<DocumentSummary>[]`), in order:
1. `title` — header "Title"; cell links to `/documents/${row.doc_id}` (React Router `Link`).
2. `category` — header "Category".
3. `doc_type` — header "Type".
4. `status` — header "Status"; render as a `Badge`. Color by status: `operative` →
   default/green; `superseded`/`repealed`/`not_yet_effective` → secondary/muted; `unknown`
   → outline. (Use `variant` + a Tailwind class; no new component.)
5. `source_index` — header "Source".
6. `official_number` — header "No." (render `—` when null).
7. `chunk_count` — header "Chunks"; right-aligned numeric.

**Filters** (controlled React state; **client-side** filtering over the fetched array —
deterministic, no server round-trips):
- **Search** `<Input>` — case-insensitive substring match against `title` **and** any entry
  in `tags` (covers the "filter by tags" requirement without a multiselect component).
- **Category** `<Select>` — options = sorted unique `category` values from the data, plus an
  "All" option.
- **Doc type** `<Select>` — unique `doc_type` values + "All".
- **Status** `<Select>` — unique `status` values + "All".
- **Source index** `<Select>` — unique `source_index` values (skip null) + "All".
- Derive option lists with `useMemo` from `data.documents`. A doc passes iff it satisfies
  every active filter (AND). Feed the filtered array to the table via `data` (do the
  filtering yourself in a `useMemo`, or use TanStack column filters — either is fine as long
  as all five controls work).
- Show a result count line: `"{n} of {total} documents"`.
- A "Clear filters" `<Button variant="outline">` resets all controls.

Loading/error: reuse Phase 0 patterns (`isLoading` → "Loading…", `error` → red text).

### C3. Corpus detail — `src/routes/CorpusDetail.tsx`

`const { docId } = useParams()`. Guard `docId` (it's `string | undefined` under strict TS):
if absent, render "Not found". Query with `enabled: Boolean(docId)`:

```ts
const { data, isLoading, error } = useQuery({
  queryKey: ["document", docId],
  queryFn: () => getDocument(docId!),
  enabled: Boolean(docId),
});
```

Handle the 404: `getDocument` throws on non-200; surface `error` as "Document not found."

**Layout:**
1. **Header** — `data.title` (`h1`), plus a `Badge` row: `category`, `doc_type`, `status`
   (same status-color mapping as the list), and `official_number` when present.
2. **Metadata block** — a definition-style grid: source URL (external `<a target="_blank"
   rel="noreferrer">`), `source_index`, `availability`, `structure`, `approval_date`,
   `effectivity_date`, `last_fetched`, `content_length`, `extraction_method`. Omit rows whose
   value is null/empty. Render `tags` as small `Badge`s.
3. **Amendment edges** — when any of `amends`/`repeals`/`supersedes`/`implements` is
   non-empty, list them under a labeled sub-heading (comma-joined source_ids per relation).
   Skip the whole section if all four are empty.
4. **Normalized text** — inside shadcn `ScrollArea` (fixed max height, e.g. `h-[50vh]`), the
   `normalized_text` in a `whitespace-pre-wrap` block. If empty, show "No normalized text."
5. **Chunks (collapsible)** — a `<Button>` toggling `showChunks` state. When first opened,
   fire a **second** query:
   ```ts
   const { data: chunkData } = useQuery({
     queryKey: ["chunks", docId],
     queryFn: () => listChunks(docId!),
     enabled: Boolean(docId) && showChunks,
   });
   ```
   Render `chunkData.chunk_count` and a list: each chunk shows `chunk_index`, a text preview
   (first ~200 chars), `char_count`/`token_estimate`, and `qdrant_id` (monospace). This is a
   lightweight preview — full chunk inspection is Phase 4 (Retrieval Lab).
6. A back `<Link to="/">` ("← Corpus").

---

## Part D — Tests

### D1. Unit — `src/routes/CorpusList.test.tsx`

Render `CorpusList` inside a `QueryClientProvider` + `MemoryRouter`, with `listDocuments`
mocked (`vi.mock("@/api/client", ...)` returning ~3 docs spanning ≥2 categories/statuses).
Assert:
- all rows render initially (result count shows total),
- typing a title substring in the search input narrows rows,
- selecting a category value narrows rows.

Use `@testing-library/react` `findBy*`/`userEvent` (or `fireEvent`). Keep the mock payload
shape-accurate to `DocumentSummary`.

Also extend `src/api/client.test.ts` with a test that `getDocument("abc")` calls
`/api/documents/abc` (assert the `fetch` mock's URL arg) and returns the typed shape.

### D2. E2E mocked — `e2e/corpus.mocked.spec.ts`

Default lane, **no backend**. Intercept:
- `**/api/documents` → `{ documents: [ …≥2 docs… ] }`
- `**/api/documents/<id>` → a `DocumentDetail` fixture (include `normalized_text`, one
  amendment edge).
- `**/api/documents/<id>/chunks` → `{ doc_id, chunk_count: 1, chunks: [ … ] }`

Assert: list renders rows; applying a filter narrows rows; clicking a title navigates to
`/documents/<id>` and the detail heading + normalized text render; opening the chunks toggle
shows the chunk. Mirror generated `schema.ts` shapes in the fixtures.

### D3. E2E smoke (optional, real backend) — `e2e/corpus.smoke.spec.ts`

`--project=smoke`, run manually/nightly with the backend up: load `/`, assert ≥1 row, click
the first title, assert the detail page shows normalized text. No mocks.

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `GET /documents` rows include `status`, `source_index`, `official_number`, `tags`.
- [ ] `GET /documents/{doc_id}` returns metadata + `normalized_text` + version fields +
      amendment edges; unknown id → **404**.
- [ ] `GET /documents/{doc_id}/chunks` returns `{doc_id, chunk_count, chunks[]}`; unknown id → 404.
- [ ] `/openapi.json` exposes `DocumentDetail`, `ChunkSummary`, `ChunkListResponse`; `/docs`
      + `/redoc` render all three documents GETs.

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` regenerates `src/api/schema.ts` clean; committed.
- [ ] `npm run typecheck` clean under strict + `noUncheckedIndexedAccess` +
      `noImplicitReturns` + `exactOptionalPropertyTypes`, including any patched
      `src/components/ui/**`.
- [ ] `npm run dev` + backend up: `/` shows the document table with working search +
      category/doc_type/status/source filters and a result count; clicking a title opens
      `/documents/:docId` with metadata, normalized text, and a working chunks toggle.
- [ ] `npm run test:unit` passes (list filter test + client tests).
- [ ] `npx playwright test --project=mocked` passes with **no backend running**.
- [ ] `src/api/client.ts` derives all types from `@/api/schema` — no hand-written request/
      response interfaces.

## Out of scope (do NOT build here)

- Chat / `/query/ask` and conversations (Phase 2).
- Dashboard, Ingestion, Health, Sources page (Phase 3).
- Retrieval Lab / full chunk inspection, trace/log views (Phase 4).
- Editing/sync-trigger UI (the `POST /documents/sync` route exists but Phase 1 adds no button).
- New shadcn components beyond the six from Phase 0; global nav chrome beyond the single
  header link; dark mode; auth.
- Touching or retiring the Streamlit app.

## Handoff notes for the executor

- `gen:types` and the `smoke` lane need the backend on `:8000`; `mocked` + `test:unit` must not.
- Under `exactOptionalPropertyTypes`, optional props on shadcn/your components need explicit
  `| undefined`; `useParams` `docId` is `string | undefined` — guard before use, never `!`
  without the `enabled` guard already shown.
- If a shadcn `ui/` file errors under strict TS, fix inline — never loosen tsconfig or add
  `// @ts-ignore`.
- Report back: `tsc --noEmit` output; the three curl results (list enrichment keys, a detail
  `normalized_text` presence, a chunk count); and `test:unit` + `--project=mocked` results.
```
