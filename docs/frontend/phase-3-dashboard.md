# Phase 3 — Dashboard + Ingestion + Health

**Goal.** Add three operational pages over new typed endpoints: a **Dashboard** (corpus/index
stats + config summary + health badge), an **Ingestion** page (sync-run history + a working
Run-sync button with deterministic completion polling), and a **Health** page (service status).
This phase also hardens the sync lifecycle so the frontend has a sound completion contract.

**Executor guidance.** Backend code below is exact — type it verbatim (field names/types are
the codegen contract). Frontend code is pinned for the client, routing, response models, and
the sync-polling contract; fill idiomatic React/JSX around the given shapes. Do not add
endpoints, pages, or shadcn components beyond those listed. Keep business logic in Python
service modules — routes stay thin adapters. Stop at the acceptance checks.

**Preconditions.**
- Phase 0–2 complete and green.
- Backend runs locally: `uvicorn app.api.main:app --reload` on `:8000` against a synced +
  indexed DB (so `documents`/`chunks`/`sync_runs` are non-empty and Qdrant has points).
- Working dir for all `npm` commands is `frontend/`.
- shadcn present: `table button badge input select scroll-area textarea card separator`.
  **Phase 3 adds NO new shadcn components** — build with those + divs/Tailwind.

---

## Part A — Backend

Four endpoints (`/stats/overview`, `/sync/runs`, `/config`, typed `/health`) + a sync-lifecycle
change so `POST /documents/sync` returns a `sync_run_id` and every run reaches a terminal DB row.

### A0. Context — what exists

- `run_sync()` (`app/sync_service.py:89`) mints `sync_run_id` at start but writes the `sync_runs`
  row **only at the end** via `_write_sync_run` (`sync_service.py:148`), hardcoding
  `status="completed"` (`sync_service.py:77`). Any failure before the final insert → **no row** →
  a poller waiting for it hangs. Phase 3 fixes this.
- `POST /documents/sync` (`app/api/routes_documents.py:98`) schedules `run_sync` as a background
  task and returns `{status: "sync started"}` — no id.
- `/health` (`app/api/health_query.py`) returns a **bare dict** `{status, qdrant, ollama}` (no
  `response_model`); it infers the generator backend from `llm_model` naming inline.
- `Settings` (`app/config.py`) has ~93 fields incl. two `SecretStr` (`qdrant_api_key:36`,
  `anthropic_api_key:116`). `resolve_embedding_config` (a validator) populates
  `embedding_model`/`embedding_dim` in place at init, so both are non-None at runtime.
- No `/stats`, `/config`, `/sync/runs` routes and no `list_sync_runs` reader exist yet.

### A1. Sync lifecycle — start-row + terminal-status finalize — `app/sync_service.py`

Replace the end-only insert with a **start row** (`status="running"`) created up front and a
**finalize UPDATE** that always runs (even on an early throw). No schema migration needed —
`sync_runs` already has `status`, nullable `completed_at`, and the count columns.

Add three helpers and rewrite `run_sync` to accept an optional caller-supplied id and to finalize
in a `try/finally`. `_create_sync_run_if_absent` uses `INSERT OR IGNORE` so the API pre-create
(A5) and the CLI-path create can both call it for the same id without colliding — whichever runs
first wins, the second is a no-op:

```python
def _create_sync_run_if_absent(sync_run_id: str, started_at: str) -> None:
	"""Insert a 'running' row if one doesn't already exist for this id (idempotent)."""
	conn = get_connection()
	try:
		conn.execute(
			"""
			INSERT OR IGNORE INTO sync_runs(
				sync_run_id, started_at, completed_at, status,
				scanned_count, changed_count, unchanged_count, failed_count
			) VALUES (?, ?, NULL, 'running', 0, 0, 0, 0);
			""",
			[sync_run_id, started_at],
		)
		conn.commit()
	finally:
		conn.close()


def _finalize_status(counts: dict, crashed: bool) -> str:
	if crashed:
		return "failed"
	succeeded = counts["changed"] + counts["unchanged"] + counts["refreshed"] + counts["reindexed_meta"]
	if counts["failed"] and succeeded == 0:
		return "failed"
	if counts["failed"]:
		return "partial"
	return "completed"


def _finalize_sync_run(sync_run_id: str, counts: dict, status: str) -> None:
	conn = get_connection()
	try:
		conn.execute(
			"""
			UPDATE sync_runs SET
				completed_at = ?, status = ?,
				scanned_count = ?, changed_count = ?,
				unchanged_count = ?, failed_count = ?
			WHERE sync_run_id = ?;
			""",
			[
				datetime.now(timezone.utc).isoformat(), status,
				counts["scanned"], counts["changed"],
				counts["unchanged"] + counts["refreshed"] + counts["reindexed_meta"],
				counts["failed"], sync_run_id,
			],
		)
		conn.commit()
	finally:
		conn.close()
```

Rewrite `run_sync` (keep the per-source loop body from `sync_service.py:96-146` **unchanged** —
only the id handling, the start-row, and the finalize wrapper change):

```python
def run_sync(sync_run_id: str | None = None) -> dict:
	sync_run_id = sync_run_id or str(uuid4())
	counts = _empty_counts()
	started_at = datetime.now(timezone.utc).isoformat()

	# Idempotent: the API pre-creates the running row (so the first poll finds it);
	# CLI callers pass no id, so create it here. INSERT OR IGNORE covers both.
	_create_sync_run_if_absent(sync_run_id, started_at)
	logger.info("sync_started", sync_run_id=sync_run_id)

	crashed = False
	try:
		sources = load_allowed_sources()
		for source in sources:
			counts["scanned"] += 1
			... # UNCHANGED loop body (sync_service.py:98-146)
	except Exception as e:
		crashed = True
		logger.warning("sync_crashed", sync_run_id=sync_run_id, error=str(e), exc_info=True)
	finally:
		status = _finalize_status(counts, crashed)
		_finalize_sync_run(sync_run_id, counts, status)
		logger.info("sync_completed", sync_run_id=sync_run_id, status=status, **counts)

	return {**counts, "sync_run_id": sync_run_id, "status": status}
```

> Delete the old `_write_sync_run` (`sync_service.py:57`) — the finalize UPDATE replaces it. The
> `load_allowed_sources()` call now lives **inside** the `try`, so an early failure still
> finalizes the row as `failed`. There is exactly one running-row-insert helper
> (`_create_sync_run_if_absent`); both the route and `run_sync` call it — do not duplicate the
> INSERT.

**Concurrency note (known limit, do not fix here):** two overlapping `run_sync` calls create two
`running` rows. The UI disables the button while polling to prevent single-client double-fires;
multi-client concurrency is out of scope for a frontend phase.

### A2. Readers — `app/db.py`

```python
def list_sync_runs(limit: int = 20) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
                SELECT sync_run_id, started_at, completed_at, status,
                       scanned_count, changed_count, unchanged_count, failed_count
                FROM sync_runs
                ORDER BY started_at DESC
                LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def corpus_counts() -> dict:
    """Cheap SQLite aggregates for the dashboard (never touches Qdrant)."""
    conn = get_connection()
    try:
        doc_total = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        doc_enabled = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE enabled = 1"
        ).fetchone()["n"]
        chunk_total = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        conversation_total = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations"      # sessions, NOT turns
        ).fetchone()["n"]
        by_category = [
            {"category": r["category"], "count": r["n"]}
            for r in conn.execute(
                "SELECT category, COUNT(*) AS n FROM documents "
                "GROUP BY category ORDER BY category"
            ).fetchall()
        ]
        return {
            "documents_total": doc_total,
            "documents_enabled": doc_enabled,
            "chunks_total": chunk_total,
            "conversations_total": conversation_total,
            "by_category": by_category,
        }
    finally:
        conn.close()
```

### A3. Stats service — new file `app/stats_service.py`

Combines SQLite aggregates + a **narrowly-guarded** Qdrant point count (returns `None` on any
Qdrant issue — missing collection, bad key, timeout, config error) + the latest sync summary.
`/stats/overview` must never 500 because Qdrant is down.

```python
from app.config import settings
from app.db import corpus_counts, list_sync_runs


def _qdrant_point_count() -> int | None:
    """Best-effort exact count of points in the collection. None if Qdrant is unavailable."""
    try:
        from app.indexing.vector_store import get_qdrant_client

        result = get_qdrant_client().count(
            collection_name=settings.qdrant_collection, exact=True
        )
        return int(result.count)
    except Exception:
        return None


def stats_overview() -> dict:
    counts = corpus_counts()
    runs = list_sync_runs(limit=1)
    return {
        **counts,
        "qdrant_points": _qdrant_point_count(),
        "last_sync": runs[0] if runs else None,
    }
```

### A4. Config view — allowlist — new function in `app/config.py`

Build from **named attributes only** — never iterate `Settings`, never touch `SecretStr` fields.
Add an explicit computed `generator_backend` (matches the inference in `health_query.py:13`).

```python
def config_view() -> dict:
    """Curated, secret-free config for the dashboard. Named allowlist only."""
    return {
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,     # populated by resolve_embedding_config
        "embedding_dim": settings.embedding_dim,
        "llm_model": settings.llm_model,
        "generator_backend": "anthropic" if settings.llm_model.startswith("claude") else "ollama",
        "reranker_backend": settings.reranker_backend,
        "qdrant_collection": settings.qdrant_collection,
        "qdrant_url": settings.qdrant_url,
        "ollama_base_url": settings.ollama_base_url,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "min_chunks_for_answer": settings.min_chunks_for_answer,
        "max_conversation_turns": settings.max_conversation_turns,
        "router_enabled": settings.router_enabled,
        "edge_expansion_enabled": settings.edge_expansion_enabled,
        "answerability_gate_enabled": settings.answerability_gate_enabled,
        "enable_query_rewriting": settings.enable_query_rewriting,
        "faithfulness_selfcheck_enabled": settings.faithfulness_selfcheck_enabled,
        "later_enacted_preference_enabled": settings.later_enacted_preference_enabled,
        "aws_region": settings.aws_region,
    }
```

> Do **not** expose `db_path`, `qdrant_api_key`, `anthropic_api_key`, or any field not listed.
> If a reviewer wants a field added later, it goes in this allowlist explicitly.

### A5. Response models + routes

**`app/api/routes_stats.py`** (new):

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.stats_service import stats_overview

router = APIRouter(prefix="/stats", tags=["stats"])


class CategoryCount(BaseModel):
    category: str
    count: int


class SyncRunSummary(BaseModel):
    sync_run_id: str
    started_at: str | None = None
    completed_at: str | None = None
    status: str | None = None
    scanned_count: int | None = None
    changed_count: int | None = None
    unchanged_count: int | None = None
    failed_count: int | None = None


class StatsOverview(BaseModel):
    documents_total: int
    documents_enabled: int
    chunks_total: int
    conversations_total: int
    qdrant_points: int | None = None
    by_category: list[CategoryCount] = []
    last_sync: SyncRunSummary | None = None


@router.get("/overview", response_model=StatsOverview, summary="Dashboard corpus + index stats")
def overview() -> StatsOverview:
    return StatsOverview(**stats_overview())
```

**`app/api/routes_sync.py`** (new) — `SyncRun` reuses the same field set as `SyncRunSummary`;
re-import it to keep one schema:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes_stats import SyncRunSummary
from app.db import list_sync_runs

router = APIRouter(prefix="/sync", tags=["sync"])


class SyncRunListResponse(BaseModel):
    runs: list[SyncRunSummary]


@router.get("/runs", response_model=SyncRunListResponse, summary="Recent sync runs")
def runs(limit: int = 20) -> SyncRunListResponse:
    return SyncRunListResponse(runs=list_sync_runs(limit))
```

**`app/api/routes_config.py`** (new):

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import config_view

router = APIRouter(prefix="/config", tags=["config"])


class ConfigView(BaseModel):
    embedding_backend: str
    embedding_model: str | None = None
    embedding_dim: int | None = None
    llm_model: str
    generator_backend: str
    reranker_backend: str
    qdrant_collection: str
    qdrant_url: str
    ollama_base_url: str
    chunk_size: int
    chunk_overlap: int
    min_chunks_for_answer: int
    max_conversation_turns: int
    router_enabled: bool
    edge_expansion_enabled: bool
    answerability_gate_enabled: bool
    enable_query_rewriting: bool
    faithfulness_selfcheck_enabled: bool
    later_enacted_preference_enabled: bool
    aws_region: str


@router.get("", response_model=ConfigView, summary="Curated, secret-free runtime config")
def config() -> ConfigView:
    return ConfigView(**config_view())
```

**Typed `/health`** — `app/api/health_query.py`. Add a `response_model` + `generator_backend`;
keep the existing health logic:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.runtime.health import ping_url, qdrant_ok

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    status: str
    qdrant: bool
    ollama: bool | None = None
    generator_backend: str


@router.get("", response_model=HealthStatus)
def healthcheck() -> HealthStatus:
    qdrant_healthy = qdrant_ok()
    uses_ollama = settings.embedding_backend == "ollama" or not settings.llm_model.startswith("claude")
    ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version") if uses_ollama else None
    healthy = qdrant_healthy and (ollama_ok is not False)
    return HealthStatus(
        status="ok" if healthy else "degraded",
        qdrant=qdrant_healthy,
        ollama=ollama_ok,
        generator_backend="anthropic" if settings.llm_model.startswith("claude") else "ollama",
    )
```

**`POST /documents/sync`** — return a server-created `sync_run_id`; pre-create the running row so
the first poll finds it. Edit `app/api/routes_documents.py`:

```python
import uuid
from app.sync_service import run_sync, _create_sync_run_if_absent  # add
from datetime import datetime, timezone

class SyncStartedResponse(BaseModel):
    status: str
    sync_run_id: str

@router.post("/sync", response_model=SyncStartedResponse, summary="Trigger a background sync")
def sync(background_tasks: BackgroundTasks) -> SyncStartedResponse:
    sync_run_id = str(uuid.uuid4())
    _create_sync_run_if_absent(sync_run_id, datetime.now(timezone.utc).isoformat())
    background_tasks.add_task(run_sync, sync_run_id)
    return SyncStartedResponse(status="sync started", sync_run_id=sync_run_id)
```

> Importing the `_`-prefixed `_create_sync_run_if_absent` across modules is a deliberate small
> exception so the route can guarantee the row exists **before** returning. It's the same single
> helper defined in A1 — do not redefine it here.

**Register routers** in `app/api/main.py`:

```python
from app.api.routes_stats import router as stats_router
from app.api.routes_sync import router as sync_router
from app.api.routes_config import router as config_router
...
app.include_router(stats_router)
app.include_router(sync_router)
app.include_router(config_router)
```

### A6. Verify backend

```bash
uvicorn app.api.main:app --reload

curl -s localhost:8000/stats/overview | python -m json.tool | grep -E 'documents_total|chunks_total|conversations_total|qdrant_points|by_category|last_sync'
curl -s localhost:8000/config | python -c "import sys,json;d=json.load(sys.stdin);print('keys',len(d));assert 'anthropic_api_key' not in d and 'db_path' not in d;print('generator_backend',d['generator_backend'])"
curl -s localhost:8000/health | python -m json.tool | grep -E 'status|qdrant|ollama|generator_backend'
curl -s localhost:8000/sync/runs | python -c "import sys,json;print('runs',len(json.load(sys.stdin)['runs']))"

# lifecycle: trigger returns an id; a running row exists immediately; it reaches a terminal status
SID=$(curl -s -X POST localhost:8000/documents/sync | python -c "import sys,json;print(json.load(sys.stdin)['sync_run_id'])")
curl -s localhost:8000/sync/runs | python -c "import sys,json,os;runs=json.load(sys.stdin)['runs'];r=[x for x in runs if x['sync_run_id']==os.environ['SID']][0];print('status',r['status'])" SID=$SID
# re-check after the sync finishes → status is completed/partial/failed, completed_at set

curl -s localhost:8000/openapi.json | python -m json.tool | grep -E 'StatsOverview|SyncRunSummary|ConfigView|HealthStatus'
```

**Backend acceptance:**
- `/stats/overview` returns corpus/chunk/conversation counts + `by_category` + `last_sync`;
  `qdrant_points` is an int when Qdrant is up and **`null` (no 500)** when it's down.
- `/config` returns only the allowlisted fields, includes `generator_backend`, and contains
  **no** `qdrant_api_key` / `anthropic_api_key` / `db_path`.
- `/health` carries a `response_model` incl. `generator_backend`.
- `/sync/runs` lists runs newest-first. `POST /documents/sync` returns a `sync_run_id`; a
  `running` row is queryable immediately and transitions to a terminal status
  (`completed`/`partial`/`failed`) — never absent.
- `/openapi.json` exposes `StatsOverview`, `CategoryCount`, `SyncRunSummary`,
  `SyncRunListResponse`, `ConfigView`, `HealthStatus`.

---

## Part B — Frontend: regen types + typed client

### B1. Regenerate types (backend up)

```bash
npm run gen:types        # rewrites src/api/schema.ts ONLY
```
Commit the regenerated `src/api/schema.ts`.

### B2. Client — extend `src/api/client.ts`

Add types + getters + the sync trigger, all derived from `paths`. Reuse the Phase 2 `apiGet`/
`apiPost`.

```ts
type StatsOverview =
  paths["/stats/overview"]["get"]["responses"]["200"]["content"]["application/json"];
type ConfigView =
  paths["/config"]["get"]["responses"]["200"]["content"]["application/json"];
type HealthStatus =
  paths["/health"]["get"]["responses"]["200"]["content"]["application/json"];
type SyncRunListResponse =
  paths["/sync/runs"]["get"]["responses"]["200"]["content"]["application/json"];
type SyncStartedResponse =
  paths["/documents/sync"]["post"]["responses"]["200"]["content"]["application/json"];

export type SyncRun = SyncRunListResponse["runs"][number];

export function getStats(): Promise<StatsOverview> {
  return apiGet<StatsOverview>("/stats/overview");
}
export function getConfig(): Promise<ConfigView> {
  return apiGet<ConfigView>("/config");
}
export function getHealth(): Promise<HealthStatus> {
  return apiGet<HealthStatus>("/health");
}
export function listSyncRuns(): Promise<SyncRunListResponse> {
  return apiGet<SyncRunListResponse>("/sync/runs");
}
export function startSync(): Promise<SyncStartedResponse> {
  return apiPost<SyncStartedResponse>("/documents/sync", {});
}
```

---

## Part C — Frontend: routing + pages

### C1. Router + nav — `src/App.tsx`

Keep `/` = `CorpusList` (unchanged). Add `/dashboard`, `/ingestion`, `/health` routes and nav
links. Dashboard is prominent in nav but **not** the default route.

```tsx
// nav links: Corpus (/), Chat (/chat), Dashboard (/dashboard), Ingestion (/ingestion), Health (/health)
<Route path="/dashboard" element={<Dashboard />} />
<Route path="/ingestion" element={<Ingestion />} />
<Route path="/health" element={<Health />} />
```

### C2. Dashboard — `src/routes/Dashboard.tsx`

Three queries: `getStats`, `getConfig`, `getHealth`. Layout with shadcn `card`:
1. **Stat cards row** — Documents (`documents_enabled`/`documents_total`), Chunks
   (`chunks_total`), Qdrant points (`qdrant_points`, render `—` when null with a muted
   "Qdrant unavailable" note), Conversations (`conversations_total`).
2. **By category** — a small `table` or badge list from `by_category`.
3. **Health badge** — from `getHealth`: `status` (`ok` → green `Badge`, `degraded` → muted/red),
   plus `qdrant`/`ollama` booleans and `generator_backend`.
4. **Config summary card** — from `getConfig`: a definition grid of the allowlisted fields
   (group loosely: models/backends, chunking, feature flags). Booleans render as on/off badges.
5. **Last sync** — from `stats.last_sync` (when present): status + counts + `completed_at`, with
   a `Link to="/ingestion"` ("View sync history").

Loading/error: reuse the established `isLoading`/`error` patterns.

### C3. Ingestion — `src/routes/Ingestion.tsx`

**Data:** `useQuery({ queryKey: ["syncRuns"], queryFn: listSyncRuns, refetchInterval })` where
`refetchInterval` is a function returning `2500` while a sync is in flight, else `false`.

**Run-sync flow (deterministic completion via the returned id):**
```ts
const [watchId, setWatchId] = useState<string | null>(null);

const start = useMutation({
  mutationFn: startSync,
  onSuccess: (res) => setWatchId(res.sync_run_id),  // begin watching this exact run
});

const runsQuery = useQuery({
  queryKey: ["syncRuns"],
  queryFn: listSyncRuns,
  refetchInterval: () => (watchId ? 2500 : false),
});

// stop watching when the watched run reaches a terminal status
const watched = runsQuery.data?.runs.find((r) => r.sync_run_id === watchId);
useEffect(() => {
  if (watched && watched.status !== "running") setWatchId(null);
}, [watched]);
```
- Button: disabled while `start.isPending || watchId`. Label "Run sync" → "Syncing…" while
  watching.
- **Client-side timeout:** if `watchId` stays non-null > ~120s, clear it and show a "Sync is
  taking longer than expected — check back shortly." notice (do not poll forever). Implement with
  a `setTimeout` armed when `watchId` is set, cleared on terminal/unmount.
- `start.isError` → red inline error.

**History table** (`table`): rows from `runs`, newest-first — `started_at`, `completed_at`,
`status` (Badge: `completed` green, `partial` amber/secondary, `failed` red, `running` muted),
`scanned/changed/unchanged/failed` counts. The in-flight `running` row appears here naturally.

### C4. Health — `src/routes/Health.tsx`

`getHealth` (+ optionally `getConfig` for context). A compact status panel: overall `status`
badge, then a row per service — Qdrant (`qdrant` bool), Ollama (`ollama` bool/null → "n/a"),
Generator (`generator_backend`). Green/red dots via Badge variants. A manual "Refresh" button
(`refetch`).

---

## Part D — Tests

### D1. Unit — extend `src/api/client.test.ts`
- `getStats()` GETs `/api/stats/overview`; `getConfig()` → `/api/config`; `startSync()` POSTs to
  `/api/documents/sync` and returns `{status, sync_run_id}`.

### D2. Unit — `src/routes/Ingestion.test.tsx` (or a dashboard render test)
- Mock `listSyncRuns` + `startSync`. Assert: clicking Run-sync calls `startSync`; when a
  subsequent `listSyncRuns` returns the watched id with `status:"completed"`, the button
  re-enables and the row shows "completed". (Fake timers / `queryClient` refetch, or assert the
  terminal-state effect directly.)

### D3. E2E mocked — `e2e/dashboard.mocked.spec.ts`
No backend. Intercept `**/api/stats/overview`, `**/api/config`, `**/api/health`,
`**/api/sync/runs`, `POST **/api/documents/sync`. Assert:
- Dashboard renders stat cards, the health badge, and the config summary.
- **Qdrant-down path:** a `stats` fixture with `qdrant_points:null` renders `—`/"unavailable",
  not a crash.
- **Ingestion Run-sync:** intercept POST → `{sync_run_id:"run-1"}`; first `/sync/runs` returns
  `run-1` as `running`, a later interception returns it `completed`; assert the button goes
  "Syncing…" → re-enabled and the row shows completed. Mirror generated `schema.ts` shapes.

### D4. E2E smoke (optional, real backend) — `e2e/dashboard.smoke.spec.ts`
`--project=smoke`: load `/dashboard`, assert stat cards render with non-zero docs; load
`/health`, assert a status badge.

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `POST /documents/sync` returns `{status, sync_run_id}`; the `sync_run_id` is queryable in
      `/sync/runs` as `running` **immediately** and reaches `completed`/`partial`/`failed` —
      never absent, even if `load_allowed_sources()` throws.
- [ ] `sync_runs` status is `partial` when some sources failed, `failed` when all failed or the
      run crashed, else `completed`.
- [ ] `/stats/overview` returns counts + `by_category` + `last_sync`; `qdrant_points` is `null`
      (no 500) when Qdrant is down; `conversations_total` counts **sessions**.
- [ ] `/config` exposes only the allowlist (incl. `generator_backend`); **no** secrets/`db_path`.
- [ ] `/health` has a `response_model`; `/sync/runs` returns runs newest-first.
- [ ] `/openapi.json` exposes `StatsOverview`, `CategoryCount`, `SyncRunSummary`,
      `SyncRunListResponse`, `ConfigView`, `HealthStatus`.

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` regenerates `src/api/schema.ts` clean; committed.
- [ ] `npm run typecheck` clean under strict.
- [ ] `npm run dev` + backend up: `/dashboard` shows stats + health + config; `/ingestion` lists
      runs and Run-sync triggers a sync, shows "Syncing…", then re-enables when the watched run
      hits a terminal status; `/health` shows service status.
- [ ] `npm run test:unit` passes (client + ingestion tests).
- [ ] `npx playwright test --project=mocked` passes with **no backend**, incl. the Qdrant-null
      and Run-sync-completion assertions.
- [ ] `src/api/client.ts` derives all types from `@/api/schema` — no hand-written shapes.

## Out of scope (do NOT build here)
- Retrieval Lab / trace / log / observability views, full chunk inspection (Phase 4).
- Evaluations (Phase 5), Cost & Usage (Phase 6).
- Editing config from the UI (read-only), auth, dark mode.
- Server-side sync concurrency guarding (documented known limit).
- New shadcn components. Touching the Streamlit app. Changing `/` to Dashboard.

## Handoff notes for the executor
- The sync-lifecycle change touches shared backend behavior (`run_sync` runs from the CLI too).
  Keep the per-source loop body byte-for-byte; only the id/start-row/finalize wrapper changes.
  Verify `raglab sync` still prints per-source lines and writes exactly one terminal row.
- After editing `sync_service.py`, run `uv run python -m compileall app` and one real
  `raglab sync` to confirm the `running → completed` transition on the happy path, and (manually)
  a forced early failure to confirm it finalizes as `failed`.
- Keep one definition of the running-row-insert helper; don't duplicate the INSERT in the route
  and the service.
- `refetchInterval` returning `false` (not `0`) stops polling; arm the 120s timeout when
  `watchId` is set and clear it on terminal state/unmount to avoid a stuck "Syncing…".
- Report back: `tsc -b`; the A6 curl results (stats incl. `qdrant_points`, `/config` secret-free
  + `generator_backend`, the sync `running→terminal` transition, openapi exposure); `test:unit`
  + `--project=mocked` results.
