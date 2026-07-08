# Phase 5 — Evaluations (read-only + metrics diff)

**Goal.** Surface the existing eval artifacts as an operator dashboard: a list of eval runs with
headline RAGAS metrics, a per-run detail (overall + abstention + by-category + per-question rows),
and a two-run **metrics diff** ("did this change help?"). Read-only — evals stay CLI-launched.

**Executor guidance.** Backend code is exact — type it verbatim (field names/types are the codegen
contract). Frontend code is pinned for the client, routing, response models, and the metric/delta
render contract; fill idiomatic React/JSX around the shapes. Do not add endpoints, pages, or shadcn
components beyond those listed. Keep business logic in `app/eval_store.py`; routes stay thin. Stop
at the acceptance checks. **This phase touches no retrieval/indexing/generation code** — pure
additive read endpoints over `data/eval_results/`.

**Preconditions.**
- Phase 0–4 complete and green.
- `data/eval_results/manifest.jsonl` and at least one bundled run dir
  (`data/eval_results/runs/{date}/{tag}/` with `run.jsonl` + `summary.json`, ideally `scored.json`
  + `meta.json`) exist. (There are real runs on disk already.)
- Working dir for all `npm` commands is `frontend/`.
- shadcn present: `table button badge input select scroll-area textarea card separator`.
  **Phase 5 adds NO new shadcn components.**

---

## Part A — Backend

Paths match the documented plan (`docs/project_plan.md:1290`): `GET /evals/runs`,
`/evals/runs/{tag}`, `/evals/runs/{tag}/rows`, `/evals/runs/{tag}/diff`.

### A0. Context — the artifact model (do not rebuild)

`app/evals/artifacts.py` already owns paths + `manifest_row()`. Reuse it; **do not** reuse
`artifacts._read_manifest_rows()` (raw `json.loads` per line — raises on a malformed line).

- **`manifest.jsonl`** — one row per run (`artifacts.manifest_row`): `tag, date, model, label,
  questions, scored, abstention_accuracy, faithfulness, answer_relevancy, context_precision,
  context_recall, layout, run_path, summary_path, scored_path`. **`context_precision` is already
  normalized** from `llm_context_precision_with_reference` here.
- **`meta.json`** — `tag, model, model_slug, label, date, started_at, completed_at, git_sha,
  question_count, scored_count, active_config, source_files, migrated_from_legacy`. Absent for
  legacy flat-file runs.
- **`summary.json`** — `{abstention:{correct,total,accuracy}, overall:{faithfulness,
  answer_relevancy, llm_context_precision_with_reference, context_recall},
  by_category:{cat:{n, ...same 4 keys}}}`. Note the **raw** precision key here is
  `llm_context_precision_with_reference` — normalize it to `context_precision` in the service.
- **`run.jsonl`** — **all attempted** rows (`app/evals/runner.py:80`): `eval_id?, question, answer,
  contexts, ground_truth, expected_sources, category, abstained, model, …`.
- **`scored.json`** — written only when RAGAS ran, **only for scorable/non-abstained rows**
  (`app/evals/report.py:75`): `user_input, response, reference, retrieved_contexts,
  faithfulness, answer_relevancy, context_recall, llm_context_precision_with_reference`. No
  `eval_id`.

**Rows are a JOIN, not `scored.json` alone.** Read `run.jsonl` (all attempted) and left-join
metrics from `scored.json`. Join key: **`(question, ground_truth)` ≡ `(user_input, reference)`**,
present in both artifacts (prefer `eval_id` if it ever appears in both; today it doesn't).
Duplicate identical `(question, ground_truth)` rows collapse to the same metrics — documented
limit. Abstained/unscored rows appear with **null** metrics.

### A1. Service — new file `app/eval_store.py`

Owns tolerant manifest parsing, **manifest gating** (a tag absent from the manifest is unknown
everywhere, even though `artifacts.paths_for_tag` could find it on disk), artifact loading, the
row join, and the summary diff.

```python
import json
from pathlib import Path
from typing import Any

from app.evals import artifacts

_METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
_RAW_PRECISION = "llm_context_precision_with_reference"


def _parse_manifest() -> list[dict]:
    """Tolerant read of manifest.jsonl — skips malformed lines (never raises)."""
    path = artifacts.results_dir() / "manifest.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("tag"):
            rows.append(rec)
    return rows


def list_runs() -> list[dict]:
    """Manifest rows, newest-first (by date then tag; tag carries a trailing timestamp)."""
    rows = _parse_manifest()
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("tag") or "")), reverse=True)
    return rows


def _manifest_tags() -> set[str]:
    return {r["tag"] for r in _parse_manifest()}


def _norm_metrics(d: dict | None) -> dict:
    """Normalize a summary metric dict to the 4 canonical keys (nullable)."""
    d = d or {}
    return {
        "faithfulness": d.get("faithfulness"),
        "answer_relevancy": d.get("answer_relevancy"),
        "context_precision": d.get("context_precision", d.get(_RAW_PRECISION)),
        "context_recall": d.get("context_recall"),
    }


def _load_summary(tag: str) -> dict | None:
    p = artifacts.existing_path(tag, "summary")
    if p is None:
        return None
    raw = artifacts.read_json(p)
    by_cat = {}
    for cat, m in (raw.get("by_category") or {}).items():
        by_cat[cat] = {"n": (m or {}).get("n"), **_norm_metrics(m)}
    return {
        "overall": _norm_metrics(raw.get("overall")),
        "abstention": raw.get("abstention") or {},
        "by_category": by_cat,
    }


def get_run(tag: str) -> dict | None:
    """Detail: manifest-gated. Synthesizes core fields from manifest_row when meta.json absent."""
    if tag not in _manifest_tags():
        return None
    meta = artifacts.load_meta(tag)
    mrow = artifacts.manifest_row(tag)
    return {
        "tag": tag,
        "model": (meta or {}).get("model") or mrow.get("model"),
        "label": (meta or {}).get("label") or mrow.get("label"),
        "date": (meta or {}).get("date") or mrow.get("date"),
        "git_sha": (meta or {}).get("git_sha"),
        "question_count": (meta or {}).get("question_count") or mrow.get("questions"),
        "scored_count": (meta or {}).get("scored_count")
        if (meta or {}).get("scored_count") is not None
        else mrow.get("scored"),
        "summary": _load_summary(tag),
        "meta": meta,
    }


def _read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def get_rows(tag: str) -> dict | None:
    """Join of run.jsonl (all attempted) + scored.json metrics. Manifest-gated."""
    if tag not in _manifest_tags():
        return None
    paths = artifacts.paths_for_tag(tag)
    run_rows = _read_jsonl(paths.run)
    scored_index: dict[tuple, dict] = {}
    if paths.scored.exists():
        for s in artifacts.read_json(paths.scored):
            key = (s.get("user_input"), s.get("reference"))
            scored_index[key] = {
                "faithfulness": s.get("faithfulness"),
                "answer_relevancy": s.get("answer_relevancy"),
                "context_precision": s.get(_RAW_PRECISION),
                "context_recall": s.get("context_recall"),
            }
    rows = []
    scored_count = 0
    for r in run_rows:
        metrics = scored_index.get((r.get("question"), r.get("ground_truth")))
        if metrics:
            scored_count += 1
        rows.append({
            "eval_id": r.get("eval_id"),
            "question": r.get("question", ""),
            "answer": r.get("answer", ""),
            "category": r.get("category"),
            "abstained": bool(r.get("abstained", False)),
            "ground_truth": r.get("ground_truth"),
            "contexts": r.get("contexts") or [],
            "faithfulness": (metrics or {}).get("faithfulness"),
            "answer_relevancy": (metrics or {}).get("answer_relevancy"),
            "context_precision": (metrics or {}).get("context_precision"),
            "context_recall": (metrics or {}).get("context_recall"),
        })
    return {"tag": tag, "row_count": len(rows), "scored_count": scored_count, "rows": rows}


def _delta(a: float | None, b: float | None) -> float | None:
    return round(a - b, 4) if a is not None and b is not None else None


def diff_runs(candidate: str, baseline: str) -> dict | None:
    """Summary-to-summary diff (overall + abstention + by_category). Both manifest-gated."""
    tags = _manifest_tags()
    if candidate not in tags or baseline not in tags:
        return None
    cand = _load_summary(candidate) or {"overall": _norm_metrics(None), "abstention": {}, "by_category": {}}
    base = _load_summary(baseline) or {"overall": _norm_metrics(None), "abstention": {}, "by_category": {}}

    overall_delta = {k: _delta(cand["overall"].get(k), base["overall"].get(k)) for k in _METRIC_KEYS}
    abst = {
        "candidate": (cand["abstention"] or {}).get("accuracy"),
        "baseline": (base["abstention"] or {}).get("accuracy"),
    }
    abst["delta"] = _delta(abst["candidate"], abst["baseline"])

    by_cat: dict[str, dict] = {}
    for cat in set(cand["by_category"]) | set(base["by_category"]):
        c = cand["by_category"].get(cat)
        b = base["by_category"].get(cat)
        if c and b:
            status = "matched"
        elif c and not b:
            status = "missing_baseline"
        else:
            status = "missing_candidate"
        by_cat[cat] = {
            "status": status,
            "candidate": _norm_metrics(c) if c else None,
            "baseline": _norm_metrics(b) if b else None,
            "delta": {k: _delta((c or {}).get(k), (b or {}).get(k)) for k in _METRIC_KEYS}
            if (c and b) else None,
        }
    return {
        "candidate_tag": candidate,
        "baseline_tag": baseline,
        "overall": {"candidate": cand["overall"], "baseline": base["overall"], "delta": overall_delta},
        "abstention": abst,
        "by_category": by_cat,
    }
```

### A2. Response models + routes — new file `app/api/routes_evals.py`

```python
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.eval_store import diff_runs, get_rows, get_run, list_runs

router = APIRouter(prefix="/evals/runs", tags=["evals"])


class MetricSet(BaseModel):
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRunSummary(BaseModel):
    tag: str
    date: str | None = None
    model: str | None = None
    label: str | None = None
    questions: int | None = None
    scored: int | None = None
    abstention_accuracy: float | None = None
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRunListResponse(BaseModel):
    runs: list[EvalRunSummary]


class Abstention(BaseModel):
    correct: int | None = None
    total: int | None = None
    accuracy: float | None = None


class CategoryMetrics(MetricSet):
    n: int | None = None


class EvalSummary(BaseModel):
    overall: MetricSet
    abstention: Abstention
    by_category: dict[str, CategoryMetrics] = {}


class EvalRunDetail(BaseModel):
    tag: str
    model: str | None = None
    label: str | None = None
    date: str | None = None
    git_sha: str | None = None
    question_count: int | None = None
    scored_count: int | None = None
    summary: EvalSummary | None = None
    meta: dict[str, Any] | None = None


class EvalRow(BaseModel):
    eval_id: str | None = None
    question: str
    answer: str
    category: str | None = None
    abstained: bool = False
    ground_truth: str | None = None
    contexts: list[str] = []
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRowsResponse(BaseModel):
    tag: str
    row_count: int
    scored_count: int
    rows: list[EvalRow]


class OverallDiff(BaseModel):
    candidate: MetricSet
    baseline: MetricSet
    delta: MetricSet


class AbstentionDiff(BaseModel):
    candidate: float | None = None
    baseline: float | None = None
    delta: float | None = None


class CategoryDiff(BaseModel):
    status: Literal["matched", "missing_baseline", "missing_candidate"]
    candidate: MetricSet | None = None
    baseline: MetricSet | None = None
    delta: MetricSet | None = None


class EvalDiff(BaseModel):
    candidate_tag: str
    baseline_tag: str
    overall: OverallDiff
    abstention: AbstentionDiff
    by_category: dict[str, CategoryDiff] = {}


@router.get("", response_model=EvalRunListResponse, summary="List eval runs (manifest)")
def runs() -> EvalRunListResponse:
    return EvalRunListResponse(runs=list_runs())


@router.get("/{tag}", response_model=EvalRunDetail, summary="Eval run meta + summary")
def run_detail(tag: str) -> EvalRunDetail:
    d = get_run(tag)
    if d is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRunDetail(**d)


@router.get("/{tag}/rows", response_model=EvalRowsResponse, summary="Per-question rows (run ⨝ scored)")
def run_rows(tag: str) -> EvalRowsResponse:
    r = get_rows(tag)
    if r is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRowsResponse(**r)


@router.get("/{tag}/diff", response_model=EvalDiff, summary="Metrics diff vs a baseline run")
def run_diff(tag: str, baseline: str) -> EvalDiff:
    d = diff_runs(candidate=tag, baseline=baseline)
    if d is None:
        raise HTTPException(status_code=404, detail="eval run(s) not found")
    return EvalDiff(**d)
```

Register in `app/api/main.py`:
```python
from app.api.routes_evals import router as evals_router
...
app.include_router(evals_router)
```

> `baseline` is a **required** query param on `/diff` — a missing one → 422 automatically.

### A3. Verify backend

```bash
uvicorn app.api.main:app --reload

TAG=$(curl -s localhost:8000/evals/runs | python -c "import sys,json;r=json.load(sys.stdin)['runs'];print(r[0]['tag'] if r else '')")
echo "tag=$TAG"
curl -s "localhost:8000/evals/runs/$TAG" | python -c "import sys,json;d=json.load(sys.stdin);print('overall',d['summary']['overall'] if d['summary'] else None,'cats',list((d['summary'] or {}).get('by_category',{})))"
# rows: abstained rows present with null metrics
curl -s "localhost:8000/evals/runs/$TAG/rows" | python -c "import sys,json;d=json.load(sys.stdin);ab=[r for r in d['rows'] if r['abstained']];print('rows',d['row_count'],'scored',d['scored_count'],'abstained_with_null', all(a['faithfulness'] is None for a in ab) if ab else 'n/a')"
# diff a run against itself → all deltas 0.0
curl -s "localhost:8000/evals/runs/$TAG/diff?baseline=$TAG" | python -c "import sys,json;d=json.load(sys.stdin);print('overall_delta',d['overall']['delta'])"
# missing baseline → 422; unknown tag → 404
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8000/evals/runs/$TAG/diff"
curl -s -o /dev/null -w "%{http_code}\n" "localhost:8000/evals/runs/__nope__"
curl -s localhost:8000/openapi.json | python -m json.tool | grep -E 'EvalRunSummary|EvalRunDetail|EvalRow|EvalDiff|CategoryDiff'
```

**Backend acceptance:**
- `GET /evals/runs` lists manifest rows newest-first and **tolerates a malformed manifest line**
  (skips it, no 500).
- `GET /evals/runs/{tag}` returns synthesized core fields (from `meta.json` or `manifest_row`
  fallback) + normalized summary (`context_precision`, not the raw key); unknown/unmanifested tag
  → **404** (even if artifacts exist on disk).
- `GET /evals/runs/{tag}/rows` returns **all attempted** rows; abstained/unscored rows carry
  **null** metrics; `scored_count` ≤ `row_count`.
- `GET /evals/runs/{tag}/diff?baseline=X` returns overall + abstention + by_category deltas; a
  category present on one side only has `status: missing_baseline|missing_candidate` and
  `delta: null`; missing `baseline` param → **422**; unknown tag → **404**.
- `/openapi.json` exposes `EvalRunSummary`, `EvalRunListResponse`, `EvalRunDetail`, `EvalSummary`,
  `EvalRow`, `EvalRowsResponse`, `EvalDiff`, `CategoryDiff`, `MetricSet`.

### A4. Backend tests — `tests/unit/test_eval_store.py`

- **Malformed manifest tolerance:** write a temp `manifest.jsonl` with one valid + one broken line;
  assert `list_runs()` returns the valid row only (no raise).
- **Unmanifested disk run → 404:** a tag whose dir exists but is absent from the manifest →
  `get_run`/`get_rows`/`diff_runs` all return `None`.
- **Unscored rows present with null metrics:** a run with an abstained row in `run.jsonl` but no
  matching `scored.json` entry → that row appears with `faithfulness is None`.
- **Category diff statuses:** candidate summary with a category the baseline lacks →
  `by_category[cat].status == "missing_baseline"`, `delta is None`.

---

## Part B — Frontend: regen types + typed client

### B1. Regenerate types (backend up)
```bash
npm run gen:types        # rewrites src/api/schema.ts ONLY
```
Commit `src/api/schema.ts`.

### B2. Client — extend `src/api/client.ts`
```ts
type EvalRunListResponse =
  paths["/evals/runs"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalRunDetail =
  paths["/evals/runs/{tag}"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalRowsResponse =
  paths["/evals/runs/{tag}/rows"]["get"]["responses"]["200"]["content"]["application/json"];
type EvalDiff =
  paths["/evals/runs/{tag}/diff"]["get"]["responses"]["200"]["content"]["application/json"];

export type EvalRunSummary = EvalRunListResponse["runs"][number];
export type EvalRow = EvalRowsResponse["rows"][number];
export type { EvalRunDetail, EvalDiff };

export function listEvalRuns(): Promise<EvalRunListResponse> {
  return apiGet<EvalRunListResponse>("/evals/runs");
}
export function getEvalRun(tag: string): Promise<EvalRunDetail> {
  return apiGet<EvalRunDetail>(`/evals/runs/${encodeURIComponent(tag)}`);
}
export function getEvalRows(tag: string): Promise<EvalRowsResponse> {
  return apiGet<EvalRowsResponse>(`/evals/runs/${encodeURIComponent(tag)}/rows`);
}
export function getEvalDiff(tag: string, baseline: string): Promise<EvalDiff> {
  return apiGet<EvalDiff>(
    `/evals/runs/${encodeURIComponent(tag)}/diff?baseline=${encodeURIComponent(baseline)}`,
  );
}
```

---

## Part C — Frontend: routing + pages

### C1. Router + nav — `src/App.tsx`
Add `/evals` + `/evals/:tag` routes and an "Evals" nav link. Keep existing routes.

### C2. Metric helpers — `src/lib/metrics.ts(x)`
- `fmtMetric(x: number | null | undefined): string` → `x == null ? "—" : x.toFixed(3)`.
- `deltaClass(d: number | null | undefined): string` → green for `d > 0`, red for `d < 0`, muted
  for `0`/null (RAGAS metrics are all higher-is-better, incl. abstention accuracy).
- `fmtDelta(d)` → signed (`+0.031` / `−0.012` / `—`).

### C3. Evals list — `src/routes/Evals.tsx`
`useQuery(["evalRuns"], listEvalRuns)` → a `table`: tag (links to `/evals/${tag}`), date, model,
label, questions, scored, faithfulness, answer_relevancy, context_precision, context_recall,
abstention_accuracy — metrics via `fmtMetric`. Newest-first (already sorted server-side). Loading/
error per established patterns.

### C4. Eval detail — `src/routes/EvalDetail.tsx`
`const { tag } = useParams()`; guard. `useQuery(["evalRun", tag], () => getEvalRun(tag!), {enabled})`.
404 → "Eval run not found."
1. **Header** — tag, model, label, date, git_sha, question/scored counts (badges).
2. **Overall metric cards** — the 4 `summary.overall` metrics + `summary.abstention.accuracy`
   (with `correct/total`). `—` when summary null.
3. **By-category table** — one row per `summary.by_category` entry: `n` + the 4 metrics.
4. **Compare** — a `select` of other run tags (from a `listEvalRuns` query, excluding the current
   tag). On pick → `useQuery(["evalDiff", tag, baseline], () => getEvalDiff(tag!, baseline),
   {enabled: Boolean(baseline)})` → a delta table: overall (candidate / baseline / Δ colored via
   `deltaClass`), abstention, and by_category with `status` rendered explicitly ("n/a" for
   `missing_baseline`/`missing_candidate`, no numeric delta).
5. **Rows drill-down** — a `<Button>` toggling a second query `useQuery(["evalRows", tag], () =>
   getEvalRows(tag!), {enabled: showRows})`. Table: question, category, abstained badge, the 4
   metrics (`—` when null). Each row expands (details/summary or state) to show `answer`,
   `ground_truth`, and `contexts` (scrollable, `whitespace-pre-wrap`).
6. Back `<Link to="/evals">`.

---

## Part D — Tests

### D1. Unit — extend `src/api/client.test.ts`
- `listEvalRuns()` GETs `/api/evals/runs`; `getEvalRun("t")` → `/api/evals/runs/t`;
  `getEvalRows("t")` → `/api/evals/runs/t/rows`; `getEvalDiff("a","b")` →
  `/api/evals/runs/a/diff?baseline=b`.

### D2. Unit — `src/lib/metrics.test.ts`
- `fmtMetric(null)` → `"—"`, `fmtMetric(0.8507)` → `"0.851"`; `deltaClass(0.03)` positive,
  `deltaClass(-0.01)` negative, `deltaClass(null)` muted; `fmtDelta` signs correctly.

### D3. E2E mocked — `e2e/evals.mocked.spec.ts`
No backend. Intercept `**/api/evals/runs` (≥2 runs), `**/api/evals/runs/<tag>` (detail w/ summary +
by_category), `**/api/evals/runs/<tag>/rows` (incl. one abstained row w/ null metrics), and
`**/api/evals/runs/<tag>/diff?baseline=*` (overall deltas + one `missing_baseline` category).
Assert: list renders + links to detail; detail shows metric cards + by-category; the rows toggle
shows a row and its expandable contexts; picking a compare baseline renders the delta table with a
signed Δ and an "n/a" category. Mirror generated `schema.ts` shapes.

### D4. E2E smoke (optional, real backend) — `e2e/evals.smoke.spec.ts`
`--project=smoke`: `/evals`, assert ≥1 run row, open detail, assert overall metrics render.

---

## Acceptance criteria (all must pass)

Backend:
- [ ] `GET /evals/runs` newest-first + malformed-manifest tolerant (the `test_eval_store` bad-line
      test passes).
- [ ] `GET /evals/runs/{tag}` — synthesized core fields + normalized summary; unmanifested-but-
      on-disk tag → 404.
- [ ] `GET /evals/runs/{tag}/rows` — all attempted rows; abstained/unscored rows have null metrics.
- [ ] `GET /evals/runs/{tag}/diff?baseline=X` — overall/abstention/by_category deltas; one-sided
      category → `status` + null delta; missing `baseline` → 422; unknown tag → 404.
- [ ] `/openapi.json` exposes the eval models listed in A3.
- [ ] `tests/unit/test_eval_store.py` passes (malformed manifest, unmanifested 404, null-metric
      rows, category diff statuses).

Frontend (run in `frontend/`):
- [ ] `npm run gen:types` clean; committed.
- [ ] `npm run typecheck` clean under strict.
- [ ] `npm run dev` + backend up: `/evals` lists runs; detail shows metric cards, by-category,
      rows drill-down, and a working compare→delta table.
- [ ] `npm run test:unit` passes (client + metrics tests).
- [ ] `npx playwright test --project=mocked` passes with **no backend**.
- [ ] `src/api/client.ts` derives all types from `@/api/schema` — no hand-written shapes.

## Out of scope (do NOT build here)
- Launching / re-scoring evals from the UI; dataset management.
- Per-row regression classification (`diff_report.classify` / provision-set logic) — diff is
  summary-to-summary only.
- Editing/deleting artifacts; rebuilding the manifest from a dir scan (manifest is the list source
  of truth — an un-`update_manifest`-ed run simply won't appear; known limit).
- Cost & Usage (Phase 6). New shadcn components. Touching the Streamlit app.

## Handoff notes for the executor
- `eval_store.py` is the only place manifest lines are parsed defensively — do **not** call
  `artifacts._read_manifest_rows()` (it raises on bad lines).
- The manifest gate is load-bearing: `get_run`/`get_rows`/`diff_runs` must return `None` for a tag
  not in `_manifest_tags()`, even when `artifacts.paths_for_tag` would find dir artifacts.
- Row join key is `(question, ground_truth)` ≡ `(user_input, reference)`. Do not join on question
  alone. Metrics are nullable; abstained rows are expected in the output.
- Normalize the summary precision key (`llm_context_precision_with_reference` → `context_precision`)
  in the service; the models only know `context_precision`.
- Report back: `tsc -b`; the A3 curl results (list + detail + rows null-metric check + self-diff
  zeros + 422 + 404 + openapi); `test_eval_store` + `test:unit` + `--project=mocked` results.
