# Phase 4 — Retrieval Lab + Observability

**Goal.** Two operator surfaces over the retrieval pipeline: a **Retrieval Lab** (submit a
question, optionally force a strategy, inspect the full in-process trace — retrieved → reranked →
selected chunks with scores, strategy/knobs, router decision, prompt/latency), and
**Observability** (browse the persisted JSONL trace history + tail the app log). Lab and
Observability render the **same `TraceRecord` schema**: the Lab builds it in-process; Observability
reads it from the trace store.

**Executor guidance.** Backend code is exact — type it verbatim (field names/types are the codegen
contract). Frontend code is pinned for the client, routing, response models, and the trace-render
contract; fill idiomatic React/JSX around the shapes. Do not add endpoints, pages, or shadcn
components beyond those listed. Keep business logic in Python service modules — routes stay thin
adapters. Stop at the acceptance checks.

**Preconditions.**
- Phase 0–3 complete and green.
- Backend runs locally on `:8000` against a synced + indexed DB (Qdrant + generator up) so
  `/retrieval/inspect` returns real traces, and `data/logs/traces/*.jsonl` + `data/logs/app.log`
  exist (run at least one `raglab ask` or an eval to populate them).
- Working dir for all `npm` commands is `frontend/`.
- shadcn present: `table button badge input select scroll-area textarea card separator`.
  **Phase 4 adds NO new shadcn components** — the trace inspector uses sectioned divs, not `tabs`.

---

## Part A — Backend

### A0. Context — what exists

- `answer(question, debug, session_id, trace=True, trace_label, strategy_override)`
  (`app/retriever/answer_service.py:229`) runs the pipeline, builds a full trace record via
  `_build_trace_record` (`answer_service.py:75`), and writes it to JSONL **only when**
  `trace and settings.trace_logging_enabled` (`answer_service.py:314`). It returns the response
  dict; the trace record is **not** returned.
- The persisted trace record shape (`_build_trace_record`, `answer_service.py:92-125`):
  `trace_id, trace_label, timestamp, session_id, question, rewritten_question, stage_counts,
  retrieved_chunks[], pre_expansion_chunks[], selected_chunks[], retrieval_strategy{strategy,
  knobs}, intent_router{enabled,model,decision,skipped_reason?}, feature_flags{}, abstained,
  error, stages[], latency_ms, prompt_length, generator_model`. Each chunk (`_chunk_trace`,
  `answer_service.py:22`): `chunk_id, score, source_id, unit_label, provision_id,
  expanded_from_parent, consolidated, dedup_merged_chunk_ids, preview`.
- **Evals consume the response `debug` dict** (`app/evals/runner.py:60` runs with `debug=True`,
  reads `resp["debug"]["chunks"]` and `resp["debug"]["stages"]`). The refactor below must not add,
  remove, or rename any `debug` key and must not add a top-level `trace` key to the `answer()`
  return.
- Traces persist per-day at `{settings.log_dir}/traces/{date}.jsonl`; the app log is a single
  rotating `{settings.log_dir}/app.log` of structlog JSON lines
  (`{event, level, timestamp, logger, ...fields}`). `settings.log_dir = "data/logs"`.
- Strategies (`app/retriever/strategy.py:72`): `"default"`, `"current_law"`.
- `Source` model already defined in `app/api/routes_query.py` (Phase 2) — reuse it.

### A1. Expose the trace in-process — `run_answer` refactor — `app/retriever/answer_service.py`

Extract the body of `answer()` into a public `run_answer(...) -> tuple[dict, dict | None]` that
returns `(response, trace_record)`. `answer()` becomes a byte-compatible wrapper. **Move the
existing body verbatim** — the only changes are: (1) initialize `trace_record = None`, (2) widen
the record-build condition to include `debug_enabled`, (3) return the tuple.

```python
def run_answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    strategy_override: str | None = None,
) -> tuple[dict, dict | None]:
    # ... IDENTICAL setup through the pipeline (answer_service.py:237-302) ...
    trace_record: dict | None = None
    with trace_context(trace_id=trace_id, session_id=session_id, collector=collector):
        # ... IDENTICAL body: conversational branch / pipeline / debug stages / append_turn ...

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("answer_completed", ... )  # unchanged

        # Build the record for debug/lab callers even when disk logging is off;
        # still WRITE only when logging is enabled (behavior-preserving for answer()).
        want_record = debug_enabled or (trace and settings.trace_logging_enabled)
        if want_record:
            trace_record = _build_trace_record(
                trace_id=trace_id,
                trace_label=trace_label,
                session_id=session_id,
                original_question=question,
                rewritten_question=effective_question,
                response=response,
                selection=selection,
                prompt=prompt,
                collector=collector,
                elapsed_ms=elapsed_ms,
                strategy_name=strategy_name,
                strategy_knobs=strategy_knobs,
                router_decision=router_decision,
                router_skipped_reason=router_skipped_reason,
            )
            if trace and settings.trace_logging_enabled:
                TraceWriter().write(trace_record)

        return response, trace_record


def answer(
    question: str,
    debug: bool | None = None,
    session_id: str | None = None,
    trace: bool = True,
    trace_label: str | None = None,
    strategy_override: str | None = None,
) -> dict:
    """Backward-compatible wrapper — public return is unchanged (CLI + evals)."""
    return run_answer(
        question,
        debug=debug,
        session_id=session_id,
        trace=trace,
        trace_label=trace_label,
        strategy_override=strategy_override,
    )[0]
```

> The record is built at the **same point** as before (after the `debug["stages"]` set and
> `append_turn`), so its contents are identical to today's persisted record. `answer()` discards
> the second tuple element — evals/CLI see a byte-identical dict. Do **not** touch `_debug_trace`,
> `_package`, or any `debug` key.

### A2. Trace store — new file `app/trace_store.py`

Reads the JSONL history **defensively** — it's an append log, not a table. Skip unparseable/partial
lines; never 500 on old or malformed records.

```python
import json
from pathlib import Path

from app.config import settings


def _trace_dir() -> Path:
    return Path(settings.log_dir) / "traces"


def _iter_files_newest_first(date: str | None) -> list[Path]:
    d = _trace_dir()
    if not d.exists():
        return []
    if date:
        p = d / f"{date}.jsonl"
        return [p] if p.exists() else []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stem, reverse=True)


def _summary(rec: dict) -> dict:
    strat = rec.get("retrieval_strategy") or {}
    return {
        "trace_id": rec.get("trace_id", ""),
        "timestamp": rec.get("timestamp"),
        "trace_label": rec.get("trace_label"),
        "question": rec.get("question", ""),
        "strategy": strat.get("strategy"),
        "stage_counts": rec.get("stage_counts") or {},
        "latency_ms": rec.get("latency_ms"),
        "abstained": bool(rec.get("abstained", False)),
        "error": bool(rec.get("error", False)),
    }


def list_traces(limit: int = 50, date: str | None = None) -> list[dict]:
    """Newest-first trace summaries. Skips lines that don't parse."""
    out: list[dict] = []
    for path in _iter_files_newest_first(date):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):  # within a day-file, later lines are newer
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict) and rec.get("trace_id"):
                out.append(_summary(rec))
                if len(out) >= limit:
                    return out
    return out


def get_trace(trace_id: str) -> dict | None:
    """Full record by id, newest file first. None if not found or unparseable."""
    for path in _iter_files_newest_first(None):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict) and rec.get("trace_id") == trace_id:
                return rec
    return None
```

### A3. Log tail — new file `app/log_reader.py`

Reads only the fixed `app.log` path (no user-supplied path → no traversal). Clamp `lines`.
structlog levels are lowercase strings (`debug|info|warning|error|critical`).

```python
import json
from collections import deque
from pathlib import Path

from app.config import settings

_LEVEL_RANK = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def read_logs(lines: int = 200, level: str | None = None) -> list[dict]:
    """Last `lines` app-log entries (oldest→newest), optionally filtered to >= level."""
    lines = max(1, min(lines, 1000))
    path = Path(settings.log_dir) / "app.log"
    if not path.exists():
        return []
    min_rank = _LEVEL_RANK.get((level or "").lower(), 0)
    with path.open(encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=lines)
    out: list[dict] = []
    for raw in tail:
        raw = raw.rstrip("\n")
        if not raw:
            continue
        try:
            rec = json.loads(raw)
            entry = {
                "timestamp": rec.get("timestamp"),
                "level": rec.get("level"),
                "event": rec.get("event"),
                "logger": rec.get("logger"),
                "raw": None,
            }
        except (ValueError, TypeError):
            entry = {"timestamp": None, "level": None, "event": None, "logger": None, "raw": raw}
        rank = _LEVEL_RANK.get((entry["level"] or "").lower(), 0)
        if rank >= min_rank:
            out.append(entry)
    return out
```

### A4. Response models + routes

**`app/api/routes_retrieval.py`** (new) — validates `strategy` as an enum (bad value → 422 before
the service) and returns a typed error on hard infra failure (`trace: TraceRecord | None`, honest):

```python
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes_query import Source
from app.retriever.answer_service import run_answer

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class ChunkTrace(BaseModel):
    chunk_id: str = ""
    score: float | None = None
    source_id: str = ""
    unit_label: str = ""
    provision_id: str = ""
    expanded_from_parent: bool = False
    consolidated: str = ""
    dedup_merged_chunk_ids: list[str] = []
    preview: str = ""


class TraceRecord(BaseModel):
    trace_id: str = ""
    trace_label: str | None = None
    timestamp: str | None = None
    session_id: str | None = None
    question: str = ""
    rewritten_question: str = ""
    stage_counts: dict[str, int] = {}
    retrieved_chunks: list[ChunkTrace] = []
    pre_expansion_chunks: list[ChunkTrace] = []
    selected_chunks: list[ChunkTrace] = []
    retrieval_strategy: dict[str, Any] = {}
    intent_router: dict[str, Any] = {}
    feature_flags: dict[str, Any] = {}
    abstained: bool = False
    error: bool = False
    stages: list[Any] = []
    latency_ms: float | None = None
    prompt_length: int | None = None
    generator_model: str | None = None


class InspectRequest(BaseModel):
    question: str
    strategy: Literal["default", "current_law"] | None = None  # None = router/auto


class InspectResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    abstained: bool = False
    error: bool = False
    error_message: str | None = None
    trace: TraceRecord | None = None


@router.post("/inspect", response_model=InspectResponse, summary="Run a query and inspect its trace")
def inspect(request: InspectRequest) -> InspectResponse:
    try:
        response, trace_record = run_answer(
            request.question,
            debug=True,                 # forces the record to be built in-process
            session_id=None,            # ephemeral: no conversation turn
            trace=True,                 # also persist to JSONL → shows in Observability
            trace_label="lab",
            strategy_override=request.strategy,
        )
    except Exception as e:             # Qdrant/Ollama down, unexpected pipeline failure
        return InspectResponse(
            answer="", sources=[], abstained=False, error=True,
            error_message=str(e), trace=None,
        )
    return InspectResponse(
        answer=response["answer"],
        sources=response.get("sources", []),
        abstained=response.get("abstained", False),
        error=response.get("error", False),
        trace=TraceRecord(**trace_record) if trace_record else None,
    )
```

> `debug=True` guarantees `trace_record` is built on any **completed** run (even soft-abstain or an
> already-handled `LLMError` inside the pipeline). `trace` is `None` only on the hard-failure path
> above, paired with `error=True` + `error_message`.

**`app/api/routes_traces.py`** (new):

```python
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_retrieval import TraceRecord
from app.trace_store import get_trace, list_traces

router = APIRouter(prefix="/traces", tags=["traces"])


class TraceSummary(BaseModel):
    trace_id: str = ""
    timestamp: str | None = None
    trace_label: str | None = None
    question: str = ""
    strategy: str | None = None
    stage_counts: dict[str, int] = {}
    latency_ms: float | None = None
    abstained: bool = False
    error: bool = False


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]


@router.get("", response_model=TraceListResponse, summary="Recent trace summaries")
def traces(limit: int = 50, date: str | None = None) -> TraceListResponse:
    return TraceListResponse(traces=list_traces(limit=limit, date=date))


@router.get("/{trace_id}", response_model=TraceRecord, summary="Full trace record by id")
def trace_detail(trace_id: str) -> TraceRecord:
    rec = get_trace(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return TraceRecord(**rec)
```

**`app/api/routes_logs.py`** (new):

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.log_reader import read_logs

router = APIRouter(prefix="/logs", tags=["logs"])


class LogEntry(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    event: str | None = None
    logger: str | None = None
    raw: str | None = None


class LogResponse(BaseModel):
    entries: list[LogEntry]
    count: int


@router.get("", response_model=LogResponse, summary="Tail of the app log")
def logs(lines: int = 200, level: str | None = None) -> LogResponse:
    entries = read_logs(lines=lines, level=level)
    return LogResponse(entries=entries, count=len(entries))
```

**Register routers** in `app/api/main.py`:

```python
from app.api.routes_retrieval import router as retrieval_router
from app.api.routes_traces import router as traces_router
from app.api.routes_logs import router as logs_router
...
app.include_router(retrieval_router)
app.include_router(traces_router)
app.include_router(logs_router)
```

### A5. Verify backend

```bash
uvicorn app.api.main:app --reload

# inspect returns a real trace incl. strategy + chunk stages; strategy override honored
curl -s -X POST localhost:8000/retrieval/inspect -H 'content-type: application/json' \
  -d '{"question":"What are the penalties for theft?","strategy":"default"}' \
  | python -c "import sys,json;d=json.load(sys.stdin);t=d['trace'];print('error',d['error'],'strategy',t['retrieval_strategy'].get('strategy'),'retrieved',len(t['retrieved_chunks']),'selected',len(t['selected_chunks']),'latency',t['latency_ms'])"

# invalid strategy → 422 (enum validation, service not reached)
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/retrieval/inspect \
  -H 'content-type: application/json' -d '{"question":"x","strategy":"bogus"}'   # expect 422

# traces list + detail; unknown id → 404
TID=$(curl -s localhost:8000/traces | python -c "import sys,json;t=json.load(sys.stdin)['traces'];print(t[0]['trace_id'] if t else '')")
curl -s "localhost:8000/traces/$TID" | python -c "import sys,json;print('has_selected',len(json.load(sys.stdin)['selected_chunks']))"
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/traces/__nope__          # expect 404

# logs tail + level filter
curl -s "localhost:8000/logs?lines=50&level=warning" | python -c "import sys,json;d=json.load(sys.stdin);print('count',d['count'])"

curl -s localhost:8000/openapi.json | python -m json.tool | grep -E 'InspectResponse|TraceRecord|ChunkTrace|TraceSummary|LogEntry'
```

**Guardrail check — the preserved public/eval surface (run once, keep in the PR notes):**

```bash
# answer() return must be byte-identical: no top-level 'trace', no debug.trace_id, unchanged debug keys.
uv run python - <<'PY'
from app.retriever.answer_service import answer
r = answer("What are the penalties for theft?", debug=True, trace=False)
assert "trace" not in r, "answer() leaked a top-level trace key"
dbg = r.get("debug", {})
assert "trace_id" not in dbg, "debug dict leaked trace_id"
print("debug keys:", sorted(dbg.keys()))   # must match pre-refactor: chunks, num_*, prompt_length, pre_expansion_chunks, stages
assert "chunks" in dbg and "stages" in dbg   # the exact keys evals read
print("OK: public/eval surface preserved")
PY
```

**Backend acceptance:**
- `POST /retrieval/inspect` returns `answer + sources + a non-null TraceRecord` on a completed run
  (chunk arrays, `retrieval_strategy.strategy`, `latency_ms` populated); `strategy` override is
  honored; an invalid `strategy` → **422**; a hard infra failure → `{error:true, error_message,
  trace:null}` (not a 500).
- `GET /traces` returns newest-first summaries and **tolerates** malformed/old JSONL (bad lines
  skipped, no 500); `GET /traces/{id}` returns the full record or **404**.
- `GET /logs` tails `app.log`, filters by level, clamps `lines`.
- The guardrail script passes: `answer()` has no top-level `trace`, no `debug.trace_id`, and the
  `debug` key set is unchanged (`chunks`/`stages` present).
- `/openapi.json` exposes `InspectResponse`, `TraceRecord`, `ChunkTrace`, `TraceSummary`,
  `TraceListResponse`, `LogEntry`, `LogResponse`.

---

## Part B — Frontend: regen types + typed client

### B1. Regenerate types (backend up)
```bash
npm run gen:types        # rewrites src/api/schema.ts ONLY
```
Commit `src/api/schema.ts`.

### B2. Client — extend `src/api/client.ts`
Add types + four wrappers, all derived from `paths`. Reuse Phase 2 `apiGet`/`apiPost`.

```ts
type InspectBody =
  paths["/retrieval/inspect"]["post"]["requestBody"]["content"]["application/json"];
type InspectResponse =
  paths["/retrieval/inspect"]["post"]["responses"]["200"]["content"]["application/json"];
type TraceListResponse =
  paths["/traces"]["get"]["responses"]["200"]["content"]["application/json"];
type TraceRecord =
  paths["/traces/{trace_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type LogResponse =
  paths["/logs"]["get"]["responses"]["200"]["content"]["application/json"];

export type TraceSummary = TraceListResponse["traces"][number];
export type { TraceRecord };
export type ChunkTrace = TraceRecord["retrieved_chunks"][number];

export function inspectRetrieval(body: InspectBody): Promise<InspectResponse> {
  return apiPost<InspectResponse>("/retrieval/inspect", body);
}
export function listTraces(params?: { limit?: number; date?: string }): Promise<TraceListResponse> {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.date) q.set("date", params.date);
  const qs = q.toString();
  return apiGet<TraceListResponse>(`/traces${qs ? `?${qs}` : ""}`);
}
export function getTrace(traceId: string): Promise<TraceRecord> {
  return apiGet<TraceRecord>(`/traces/${encodeURIComponent(traceId)}`);
}
export function getLogs(params?: { lines?: number; level?: string }): Promise<LogResponse> {
  const q = new URLSearchParams();
  if (params?.lines != null) q.set("lines", String(params.lines));
  if (params?.level) q.set("level", params.level);
  const qs = q.toString();
  return apiGet<LogResponse>(`/logs${qs ? `?${qs}` : ""}`);
}
```

---

## Part C — Frontend: routing + pages

### C1. Router + nav — `src/App.tsx`
Keep existing routes. Add `/lab`, `/observability`, `/logs` + nav links.
```tsx
<Route path="/lab" element={<Lab />} />
<Route path="/observability" element={<Observability />} />
<Route path="/logs" element={<Logs />} />
```

### C2. Shared inspector — `src/components/TraceView.tsx`
A pure presentational component `TraceView({ trace }: { trace: TraceRecord })` reused by Lab and
Observability detail. Renders (sectioned divs, `card`/`badge`/`separator` — **no `tabs`**):
1. **Summary bar** — `retrieval_strategy.strategy`, `latency_ms`, `prompt_length`,
   `generator_model`, `abstained`/`error` badges, `stage_counts` (retrieved/pre_expansion/selected).
2. **Router** — `intent_router` (enabled, model, decision, skipped_reason) when present.
3. **Chunk columns** — three labeled lists: **Retrieved** (`retrieved_chunks`), **Reranked**
   (`pre_expansion_chunks`), **Selected** (`selected_chunks`). Each chunk row: `score` (fixed
   precision), `source_id`, `unit_label`, flags (`expanded_from_parent` badge, dedup count from
   `dedup_merged_chunk_ids.length`), and `preview` (monospace, `whitespace-pre-wrap`). Guard empty
   arrays with a muted "none".
4. **Feature flags** — `feature_flags` as a compact key/value grid.

### C3. Retrieval Lab — `src/routes/Lab.tsx`
- Controls: `textarea` (question), a `select` for strategy (`Auto (router)` = omit, `default`,
  `current_law`), Run `button` (disabled while pending).
- `useMutation({ mutationFn: () => inspectRetrieval({ question, strategy: strategy || null }) })`.
  Pending → "Running…" + disabled. `data.error` → red `error_message`. Else render `data.answer`
  (+ sources as small cards) and `<TraceView trace={data.trace} />` (guard `data.trace` null).
- Ephemeral: no history/session; each Run replaces the view.

### C4. Observability — `src/routes/Observability.tsx`
- `useQuery({ queryKey: ["traces"], queryFn: () => listTraces({ limit: 100 }) })` → a `table`:
  timestamp, question (truncated), strategy, latency, stage counts, abstained/error badges,
  `trace_label`. A row-select sets `selectedId` state.
- On select: `useQuery({ queryKey: ["trace", selectedId], queryFn: () => getTrace(selectedId!),
  enabled: Boolean(selectedId) })` → `<TraceView />`. 404 → "Trace not found."
- Optional `date` `input` filter feeding `listTraces({ date })`.

### C5. Logs — `src/routes/Logs.tsx`
- `useQuery({ queryKey: ["logs", level, lines], queryFn: () => getLogs({ lines, level }) })`.
- A `select` for level (all/debug/info/warning/error), a Refresh `button` (`refetch`), and a
  monospace `table`/list: timestamp, level (color badge), logger, event (or `raw` for unparseable
  lines). Show `count`.

---

## Part D — Tests

### D1. Unit — extend `src/api/client.test.ts`
- `inspectRetrieval({question:"x"})` POSTs `/api/retrieval/inspect`; `listTraces({limit:5})` GETs
  `/api/traces?limit=5`; `getTrace("t1")` GETs `/api/traces/t1`; `getLogs({level:"warning"})` GETs
  `/api/logs?level=warning`.

### D2. Unit — `src/components/TraceView.test.tsx`
- Given a `TraceRecord` fixture with 2 retrieved / 1 selected chunk, assert the three column
  headers render, a score and `source_id` show, and an `expanded_from_parent` chunk shows its badge.

### D3. E2E mocked — `e2e/lab.mocked.spec.ts`
No backend. Intercept `POST **/api/retrieval/inspect` → `{answer, sources:[…], abstained:false,
error:false, trace:{…full TraceRecord with chunks + strategy…}}`. Assert: Run renders the answer
and the trace columns with a score; selecting strategy `current_law` is sent in the POST body.

### D4. E2E mocked — `e2e/observability.mocked.spec.ts`
Intercept `**/api/traces` (≥2 summaries) + `**/api/traces/<id>` (full record) + `**/api/logs`.
Assert: the trace table lists rows; selecting one renders `TraceView`; the Logs page renders
entries and the level filter re-queries. Mirror generated `schema.ts` shapes.

### D5. E2E smoke (optional, real backend) — `e2e/lab.smoke.spec.ts`
`--project=smoke`: `/lab`, run one real question, assert an answer + ≥1 selected chunk; `/observability`, assert ≥1 trace row.

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `answer()` public return is **byte-identical** — the guardrail script passes (no top-level
      `trace`, no `debug.trace_id`, `debug` keys unchanged incl. `chunks`/`stages`).
- [ ] An eval smoke row (`run_rows` on 1 question) yields identical `selected_chunk_ids` +
      `debug_stages` before/after the refactor.
- [ ] `POST /retrieval/inspect` → non-null `TraceRecord` on a completed run; `strategy` honored;
      invalid `strategy` → 422; hard failure → `{error:true, trace:null}` (no 500).
- [ ] `GET /traces` newest-first + malformed-line tolerant; `GET /traces/{id}` → record or 404.
- [ ] `GET /logs` tails `app.log`, filters by level, clamps `lines`.
- [ ] `/openapi.json` exposes `InspectResponse`, `TraceRecord`, `ChunkTrace`, `TraceSummary`,
      `TraceListResponse`, `LogEntry`, `LogResponse`.

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` regenerates `src/api/schema.ts` clean; committed.
- [ ] `npm run typecheck` clean under strict.
- [ ] `npm run dev` + backend up: `/lab` runs a question (with strategy override) and shows the
      trace columns; `/observability` lists traces and opens a detail; `/logs` tails with a level
      filter.
- [ ] `npm run test:unit` passes (client + TraceView tests).
- [ ] `npx playwright test --project=mocked` passes with **no backend**.
- [ ] `src/api/client.ts` derives all types from `@/api/schema` — no hand-written shapes.

## Out of scope (do NOT build here)
- Editing/replaying/deleting traces; log streaming or websockets; trace search beyond
  date+limit; per-request feature-flag overrides (only `strategy` is overridable).
- Evaluations (Phase 5), Cost & Usage (Phase 6).
- New shadcn components (no `tabs` — use sectioned divs). Touching the Streamlit app.

## Handoff notes for the executor
- The `run_answer` extraction is mechanical: move `answer()`'s body verbatim, add
  `trace_record = None`, widen the build condition to `debug_enabled or (trace and
  trace_logging_enabled)`, `return response, trace_record`; make `answer()` return
  `run_answer(...)[0]`. Do not touch `_debug_trace`/`_package`/any `debug` key.
- Run the A5 guardrail script **and** one eval row before/after — this touches the shared
  generator path; the whole point is a zero-diff public surface.
- `routes_retrieval.py` imports the public `run_answer` (not a private symbol). `routes_traces.py`
  imports `TraceRecord` from `routes_retrieval` to keep one schema.
- `read_logs` reads only the fixed `app.log` path; never accept a path param.
- Report back: `tsc -b`; the A5 curl results (inspect trace + 422 + 404 + logs count + openapi);
  the guardrail-script output; `test:unit` + `--project=mocked` results.
