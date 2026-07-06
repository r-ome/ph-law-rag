# Observability & Logging Plan — ph-law-rag

**Status:** Proposed (not yet implemented)
**Date:** 2026-07-06

## Context

No logging infrastructure today — 35 scattered `print()` calls, no log files, no
request/trace IDs. There is already a rich `_debug_trace()` in
`app/retriever/answer_service.py` that captures per-stage retrieval data, but it is
only returned in the response dict when `debug=True`, then discarded. That is the seed
for real observability.

**Locked decisions:**

- Logging stack: **structlog** (`uv add structlog`).
- Trace scope: **all `answer()` calls** (serving + eval), config-gated.

## 1. New package `app/observability/`

- **`logger.py`** — `configure_logging()` called once per entry point (CLI, FastAPI
  startup, eval runner). structlog with two outputs:
  - **Console**: dev-friendly renderer, level from `settings.log_level`
    (`DEBUG` when `settings.debug`).
  - **File**: `JSONRenderer` → `data/logs/app.log` via a `RotatingFileHandler` bridge
    (`ProcessorFormatter`), `maxBytes`/`backupCount` from config.
  - `get_logger(__name__)` helper. Passing `__name__` yields `app.retriever.…`; the
    helper **rewrites the leading `app.` to `raglab.`** so all loggers sit under the
    `raglab.*` namespace (single place to set level/handlers). Modules outside `app.`
    are logged under their given name unchanged.
  - `configure_logging()` must be **idempotent**: guard with a module-level
    `_configured` flag so repeated calls from CLI subcommands, tests, Streamlit reloads,
    or re-imports don't duplicate handlers and double-log lines. When it does need to
    reset, **only manage handlers `raglab` owns** — tag each handler this module adds
    (e.g. a marker attribute) and remove only tagged handlers. Never blanket-clear the
    root logger's handlers, which would break Uvicorn/FastAPI or pytest logging.
- **`context.py`** — mint a `trace_id` (uuid) per `answer()` call and
  `structlog.contextvars.bind_contextvars(trace_id=…, session_id=…)`. Every downstream
  log line inherits it automatically — no manual threading through `select_context` →
  retriever → `generate`. Bind/clear per call (async/thread-safe) so IDs don't bleed
  across concurrent eval queries.
- **`trace.py`** — a `TraceWriter` appending one fat JSON object per query to
  `data/logs/traces/<date>.jsonl`. Separate from the app log; this is the
  "watch data move / retrace the bug" store.

## 2. Config additions (`app/config.py`, matching the plan's Config section)

```
log_dir: str = "data/logs"
log_level: str = "INFO"            # console; file always DEBUG
log_to_file: bool = True
log_max_bytes: int = 10_000_000
log_backup_count: int = 5
trace_logging_enabled: bool = True   # per-query JSONL data trace (all answer() calls)
trace_max_text_preview: int = 200    # truncate chunk text in traces
```

## 3. Per-query trace — the headline feature

Persist the existing `_debug_trace()` payload for **every** `answer()` call (when
`trace_logging_enabled`, not gated on `debug`), enriched into one record per query:

- `trace_id`, `timestamp`, `session_id`, original + rewritten question
- stage counts: `retrieved → pre_expansion → selected` (from `SelectionResult`)
- per-stage chunk lists: chunk_id, score, source_id, provision_id,
  `expanded_from_parent`, `consolidated`, `dedup_merged_chunk_ids`, text preview
- feature-flag snapshot (which of the ~15 toggles were on)
- `abstained`, `error`, per-stage latency, prompt length, generator model

**Non-user calls must opt out.** `answer()` gains two params:
`trace: bool = True` and `trace_label: str | None = None`. Internal/synthetic calls pass
`trace=False` so they never land in the trace store; `trace_label` tags genuine traces
for later filtering. The known case today: the eval runner's warmup call at
`app/evals/runner.py:62` becomes `answer("warmup", trace=False)`. Genuine eval-dataset
rows and all serving traffic keep `trace=True`.

Decouples tracing from the `debug` response flag → prod/serving traffic is observable
without leaking debug into API responses. Every eval or serving query leaves a
replayable trail answering "which change moved the data."

### 3a. Divergence from the project plan (source-of-truth update)

The project plan (`docs/project_plan.md:342`, `:1208`) scopes the retrieval trace to
**debug mode**. Unconditional per-query persistence is a deliberate **improvement**
that supersedes that expectation. Implementing this requires updating `project_plan.md`
to record the new default. Classification: *improvement + source-of-truth update*.

### 3b. Privacy & retention

Traces write **legal questions, session IDs, rewritten questions, and chunk text
previews to local disk**. This is acceptable under the local-first design but must be
bounded:

- Retention: date-rolled trace files pruned by `raglab logs prune --days N` (default
  keep window, e.g. 30 days). App log bounded by rotation (§6).
- Text previews truncated to `trace_max_text_preview` (default 200 chars) — never the
  full chunk body.
- `data/logs/` git-ignored (§6) so no legal-question content is ever committed.
- Kill switch: `trace_logging_enabled=False` disables all per-query persistence. Default
  is `True` for local dev; **cloud/serving deployments should set it `False`** (or
  shorten retention) unless the trace store is access-controlled.

### 3c. Best-effort contract (must never break serving/eval)

All logging and trace writes are **best-effort**. If `data/logs` is unwritable, disk is
full, or a value is not JSON-serializable:

- The failure is caught, downgraded to a `warning` (emitted once, not per-row to avoid
  log storms), and **`answer()` returns normally**.
- Trace serialization uses a safe encoder (`default=str`) so non-serializable values
  degrade to a string instead of raising.

### 3d. Data-collection contract (how per-stage diagnostics reach the trace)

Per-stage latency and fire/no-op flags are **not** on the current `SelectionResult`
(`app/retriever/context_selection.py` carries only `retrieved`, `pre_expansion`,
`selected`). Logging timings inside downstream functions does not make them available to
`TraceWriter`. Concrete mechanism:

- A **`TraceCollector`** object is created in `answer()` and stored in a **private
  `contextvars.ContextVar[TraceCollector | None]`** (same lifetime as `trace_id`, but a
  *separate* var). Do **not** `bind_contextvars(trace_collector=…)` — that would merge
  the collector object into every structlog event (noise + serialization hazard). Only
  `trace_id`/`session_id` go in structlog contextvars; the collector lives in its own var
  that no logging processor renders. Stage functions fetch the current collector and
  record their own timing / before-after counts / fire flags into it
  (`collector.stage("dedup", in_n, out_n, ms, fired=...)`). No ad-hoc global timing
  state.
- `answer()` reads the collector at the end and merges it into the single trace record.
- If no collector is bound (stage called outside `answer()`, e.g. `raglab retrieve` or a
  unit test), the record calls are no-ops — stages stay usable standalone.
- The plain `SelectionResult` stays unchanged; diagnostics ride the collector, not the
  result dataclass.

## 4. Instrumentation points

| Location | What to log |
|---|---|
| `answer_service.answer()` | mint trace_id; request in/out, rewrite, abstention decision, latency; write trace record |
| `context_selection.select_context()` | stage transitions + counts (core data movement) |
| `hybrid_retriever` / `reranker` | candidate counts, top scores, timing |
| `edge_expansion` / `parent_expansion` / `dedup` / `prefer_operative` | fire vs no-op + before→after counts (these fire on a minority of queries — makes "did it fire" auditable) |
| `llm_client.generate()` | model, prompt len, latency, `LLMError` at WARNING |
| `sync_service` / `index_service` | add structured diagnostic logs; keep user-facing sync progress (`[OK]/[SKIP]/[FAIL]/[META]/[REINDEX]`) on stdout (see §5). `index_service`/`chunker`/`reindex` `[WARN]` lines → `logger.warning` |

## 5. Replace the 35 `print()` calls

Draw an explicit line between **command output** (belongs on stdout, is the user's
result) and **diagnostics** (belongs in logs). Do not collapse the two.

- **Diagnostics → `logger.info/warning`**: `[WARN]` lines in `index_service.py`,
  `chunker.py`, `reindex.py`; internal pipeline state.
- **Command output → stays on stdout** (`print`), not routed through logging:
  - `app/evals/report.py` report tables.
  - `app/evals/runner.py:92,95,101` progress/timing lines — these are the eval
    command's live output.
  - `sync_service.py` `[OK]/[SKIP]/[FAIL]/[META]/[REINDEX]` per-source progress — these
    are `raglab sync`'s user-facing status.
- Where a line is *both* (sync progress a user watches **and** a diagnostic worth
  persisting), keep the stdout `print` and add a parallel `logger` call — same event,
  two sinks, not one replacing the other.

## 6. Retention / hygiene

- `data/logs/` → `.gitignore` (alongside `data/raw`, `data/qdrant`). Keeps
  legal-question content out of git.
- Rotation via `RotatingFileHandler` bounds `app.log`; traces roll by date;
  `raglab logs prune --days N` prunes old trace files (see §3b retention).

## 7. Dependency

- `uv add structlog`.

## Open item to verify before wiring

The eval runner's concurrency model, to confirm `contextvars` binding is correct under
any parallelism.
