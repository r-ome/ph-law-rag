# Pipeline refactor plan — decouple before CRAG

Status: PR1 FROZEN (2026-07-09); PR2+ draft
Owner: Jerome. Claude drafted; Jerome codes.

Goal: make each answer-pipeline stage replaceable and traceable so model cascading,
evidence-coverage checking, and eventual CRAG land as new stage implementations,
not rewrites. Async is **deferred** (see Non-goals).

Design invariants:

- Retrieval does not know which model generates. Generation does not know how retrieval ran.
- CRAG is a stage, not an app rewrite.
- Config selects policies; conditionals live inside their stage, reading the policy.
- The `local` profile is bit-identical to today's behavior — locked eval baselines survive.

---

## 1. Current state (audit, 2026-07-08)

Much of the requested decoupling already exists. Do not rebuild:

| Concern | Where it lives today | Verdict |
|---|---|---|
| Intent classification | `app/retriever/intent_router.py` (Haiku, `RouterDecision`, ADR-022) | Done. Reuse. |
| Retrieval planning | `app/retriever/strategy.py` (`StrategyPreset`, `RetrievalKnobs`, presets `default`/`current_law`) | Done. Reuse. |
| Context retrieval | `context_selection.select_context()` — hybrid → rerank → edge expansion → prefer-operative → parent expansion → dedup, each under `stage_timer` | Clean. Untouched by this refactor. |
| Model seam | `llm_client.generate(system, user, model=...)` — per-call model override, `claude*`→Anthropic else Ollama | Done. ModelRouter is policy on top. |
| Tracing | `TraceCollector` + `stage_timer` + `TraceWriter`; records intent decision, strategy+knobs, per-stage counts/latency, chunk lists, feature flags, generator model | ~80% of target. Additive deltas only. |

What is actually broken / missing:

1. **`app/retriever/answer_service.py` is the accumulation point.** `run_answer` +
   `_run_pipeline` inline every feature behind a `settings.*` flag: greeting check,
   session rewrite, router, min-chunks gate, answerability gate, generation,
   self-check, soft-abstain, session persistence, trace assembly. CRAG would add
   coverage eval + corrective loop + escalation here. This file is the refactor.
2. **Flat 40-knob `Settings`** — behavior knobs and env wiring share one class and
   one `.env`. No profiles. `RetrievalKnobs` is the embryo of the profile idea but
   retrieval-scoped only.
3. **No pipeline state object** — stages pass `(response, selection, prompt)` tuples.
4. **No evidence-sufficiency stage** — just `min_chunks_for_answer` + the
   off-by-default answerability gate. No `EvidenceReport`, no corrective slot.
5. **Model selection is global** (`settings.llm_model`); the router picks a strategy
   but never a model.

---

## 2. Target structure

New package `app/pipeline/` for orchestration; `app/retriever/` demotes to a
retrieval library. No plugin registry, no ABC hierarchy — a stage is a plain
function with a uniform signature. (Review-checklist guard: if `stages.py` grows a
`StageBase`, that's scope creep.)

**This tree is the end state after PR4.** Each PR creates only its own files:
PR1 = `state.py`/`stages.py`/`runner.py`; PR2 = `policy.py`; PR3 =
`model_router.py`; PR4 = `evidence.py`/`corrective.py`. Types land with their
consumers: PR1's `state.py` holds `AnswerState` only; `ModelChoice` arrives in
PR3, `EvidenceReport` in PR4.

```
app/pipeline/
  state.py          # AnswerState + EvidenceReport + ModelChoice
  policy.py         # AnswerPolicy + profile registry + resolve_policy()
  model_router.py   # select_model(policy, state) -> ModelChoice
  evidence.py       # evaluate_evidence(state, policy) -> EvidenceReport   (heuristic now, CRAG later)
  corrective.py     # corrective_retrieve(state, policy) -> state          (no-op slot now)
  stages.py         # rewrite_query, classify_intent, plan_retrieval, retrieve_context,
                    # gate_evidence, route_model, generate_answer, self_check, package
  runner.py         # run_answer(): ordered stage sequence + trace assembly
app/retriever/
  answer_service.py # thin shim: re-exports answer()/run_answer() from pipeline.runner
                    # (callers unchanged: routes_retrieval, routes_query, cli, evals/runner)
```

### Core types

```python
@dataclass
class AnswerState:                      # mutable, threaded through stages
    question: str
    effective_question: str
    session_id: str | None
    router_decision: RouterDecision | None
    strategy_name: str
    knobs: RetrievalKnobs
    selection: SelectionResult
    evidence: EvidenceReport | None     # CRAG slot
    corrective_ran: bool
    model_choice: ModelChoice | None
    prompt: str | None
    answer: str | None
    abstained: bool
    error: str | None

@dataclass(frozen=True)
class EvidenceReport:
    verdict: Literal["sufficient", "partial", "insufficient"]
    method: str                         # "min_chunks" | "answerability_gate" | "crag_facets" (future)
    missing_facets: list[str]           # empty until CRAG; feeds corrective retrieval
    detail: dict

@dataclass(frozen=True)
class ModelChoice:
    model: str
    reason: str                         # "policy_default" | "intent:<x>" | "evidence:partial"
```

### Runner shape

Explicit ordered sequence — no framework. Each stage wrapped in `stage_timer`;
each conditional lives inside its stage and reads the policy, not raw settings.

```python
state = init_state(question, ...)
if is_conversational(state.question):
    package_greeting(state)      # marks answer; skips retrieval/generation only
else:
    rewrite_query(state, policy)        # session-history rewrite (existing)
    classify_intent(state, policy)      # existing router; honors strategy_override
    plan_retrieval(state, policy)       # resolve strategy -> knobs (existing resolve_knobs)
    retrieve_context(state, policy)     # existing select_context, untouched
    gate_evidence(state, policy)        # min_chunks + answerability today; CRAG later
    if not state.abstained:
        if state.evidence.verdict == "partial" and policy.corrective_retrieval_enabled:
            corrective_retrieve(state, policy)   # no-op today; curated corpus only, never web
        route_model(state, policy)          # ModelChoice
        generate_answer(state, policy)      # llm_client.generate(..., model=state.model_choice.model)
        self_check(state, policy)           # existing selfcheck, policy-driven
finalize(state, policy)          # append session turn, completion log, debug stages
return response_from(state), trace_from(state)
```

No stage may return directly from `run_answer`. Greetings, hard abstentions,
LLM errors, and future corrective failures all set fields on `AnswerState`
(guarded by explicit state checks in straight-line flow), then reach the same
finalization path that today's `answer_service.run_answer` uses: session
creation/append, completion logging, debug-stage attachment, trace record
construction, and trace writing.

**No `try/finally` in PR1 (decision 2026-07-09).** The runner preserves today's
exception behavior exactly: only `LLMError` is converted into an error response;
an unexpected exception mid-pipeline propagates *without* appending a session
turn or writing a trace, exactly as today. Unified `finally`-based finalization
is introduced only if/when PR4 needs it for evidence/corrective failure
handling.

---

## 3. Configuration: profiles over flags

`.env` shrinks to secrets + environment wiring (`ANTHROPIC_API_KEY`, `QDRANT_URL`,
`OLLAMA_BASE_URL`, AWS region, paths) plus one new key: `RAGLAB_PROFILE=local`.

```python
@dataclass(frozen=True)
class AnswerPolicy:
    name: str                                # trace field
    generator_model: str                     # was llm_model
    strong_model: str | None                 # cascade target (None = no cascade)
    router_enabled: bool
    router_model: str
    escalate_intents: frozenset[str]         # intents routed to strong_model
    escalate_on_partial_evidence: bool       # future CRAG hook
    retrieval_defaults: RetrievalKnobs
    evidence_gate: Literal["min_chunks", "answerability", "crag"]  # crag = future
    min_chunks_for_answer: int
    corrective_retrieval_enabled: bool
    selfcheck_enabled: bool
    later_enacted_preference_enabled: bool
    query_decomposition_enabled: bool        # stays off (both A/Bs negative)
```

Profiles as a Python dict registry in `policy.py` (4–6 profiles don't justify a
YAML loader; frozen dataclasses validate for free):

| Profile | Intent |
|---|---|
| `local` | Mirrors today's defaults exactly. mistral, routerless, no cascade. `AnswerPolicy.from_settings()` — env overrides keep working. |
| `cloud` | Haiku router on, serving reranker pins unchanged, current demo generator. |
| `eval` | Deterministic gen, trace always on; profile name lands in eval artifacts (self-labeling by profile, not just model). |
| `cascade` | Router on, `strong_model="claude-haiku-4-5"`, `escalate_intents={"list_or_rule_synthesis", "amendment_or_current_law"}`. |
| `local-cascade` | **Experiment track (see §4).** Local router arm + local strong model; everything on-device. |
| `crag-experimental` | `evidence_gate="crag"`, corrective on. Registered; raises clearly until PR5. |

Compatibility rules (document once, surface in `show-config` and the trace):

- `Settings` keeps every existing field; `local` is built `from_settings()`.
- Non-default profile fields win over env for behavior knobs; env always wins for
  secrets/infra.
- `config_view()` gains `"profile": policy.name`.

Field ownership must be explicit in `policy.py` before PR2 lands:

- **Env/infra only:** source/data paths, SQLite path, Qdrant URL/collection/API
  key, Ollama base URL, AWS region, Anthropic key, log/eval paths, request
  timeout, embedding backend/model/dim, and reranker backend/model/region.
- **Policy-owned behavior:** generator model, router enabled/model, retrieval
  knobs, min chunks, evidence gate, corrective retrieval, self-check,
  later-enacted preference, query decomposition/packaging.
- **Local exception:** `local = AnswerPolicy.from_settings(settings)` preserves
  today's `.env` behavior exactly, so existing local A/B commands keep working.
  For named non-local profiles, policy-owned fields ignore conflicting `.env`
  behavior values; `resolve_policy()` returns a `policy_overrides`/`env_ignored`
  summary for `show-config` and traces.

PR2 acceptance includes tests for this precedence matrix: `local` mirrors raw
settings, a named profile overrides a conflicting behavior env var, and infra
env vars still win under every profile.

---

## 4. Model routing / cascading

```python
def select_model(policy: AnswerPolicy, state: AnswerState) -> ModelChoice:
    if policy.strong_model is None:
        return ModelChoice(policy.generator_model, "policy_default")
    intent = state.router_decision.routed_intent if state.router_decision else None
    if intent in policy.escalate_intents:
        return ModelChoice(policy.strong_model, f"intent:{intent}")
    if policy.escalate_on_partial_evidence and state.evidence and state.evidence.verdict == "partial":
        return ModelChoice(policy.strong_model, "evidence:partial")
    return ModelChoice(policy.generator_model, "policy_default")
```

Pure function, no I/O. Runs after `gate_evidence`, so evidence-based escalation is
free once CRAG lands. Retrieval never sees `ModelChoice`; generation never sees
`RetrievalKnobs`; the only shared object is `AnswerState`.

Out of scope (deliberately): retry cascading ("low-confidence answer → regenerate
with strong model") — doubles latency/cost and needs its own A/B; the design
supports it later as a post-`self_check` loop.

### 4a. Cloud cascade experiment (PR3)

A/B on the 81-row labeled set, `eval` discipline (deterministic gen, RAGAS row
cache, judge changed-context rows): `cascade` vs all-mistral vs all-Haiku.
Headline number: what fraction of the known Haiku faithfulness gain (+0.135,
local-vs-cloud thesis) does cascade capture, at what fraction of all-Haiku cost.
Expected escalation volume from R1 labels: synthesis + amendment intents ≈ 2 of 5
lanes.

### 4b. Local cascade experiment (PR3, new)

Everything on-device — router and strong model both local. Two questions, two
benchmarks, both using existing harnesses:

1. **Local router seat.** Re-run `scripts/classify_intent_ab.py` on the local arms
   (`mistral`, `qwen3:4b`, `gemma3:4b`; add a `gemma4:e4b` arm behind the standing
   per-release Ollama-bug tripwire — R1 addenda showed it ties Haiku on quality
   when it runs). Metric: strategy-level accuracy + **zero false escalations on
   `default` rows** (an over-eager local router silently doubles latency on easy
   questions). qwen3's 21s thinking-token latency likely disqualifies it for the
   router seat regardless of accuracy. Accept a router weaker than Haiku:
   low-confidence → default already fails safe.
2. **Local strong-generator seat.** Benchmark installed candidates as escalation
   target on the escalated-intent row subset only (synthesis + amendment rows of
   the eval set): `deepseek-r1:8b`, `qwen3:4b`, `gemma3:4b`, `gemma4:e4b`
   (tripwire) vs the `mistral` baseline. Metrics: faithfulness + recall on those
   rows, wall-clock per answer. Prior to beat: the local-vs-cloud thesis found the
   remaining gap is a *generator* property local models didn't close — so the bar
   for a local strong model is "beats mistral on hard intents," not "matches
   Haiku."

Ship `local-cascade` only if (1) a local router arm clears an acceptable
false-fire rate and (2) some local model beats mistral on the escalated subset by
more than judge noise. Otherwise record the negative result (devlog) and keep
`cascade` cloud-only.

---

## 5. CRAG readiness (not CRAG)

Ships in PR4: the `EvidenceReport` type, the `gate_evidence` stage (today's
min-chunks check and the answerability gate become two implementations behind one
interface), and `corrective_retrieve` as a no-op with its contract fixed:

- Input: `state.selection` + `evidence.missing_facets`
  (e.g. `["penalty clause", "amending statute", "age threshold"]`).
- Behavior: targeted query per missing facet against the **existing curated corpus**
  (Qdrant + BM25) — never generic web search. Merge via the existing dedup path;
  the dormant `subquery_retrieval` merge machinery is the reuse point.
- One corrective round max. `fired` + added chunks traced.

Future CRAG evaluator ("does context contain all legal ingredients?") = an LLM
call slotted as `evidence_gate="crag"` — a third implementation of the same stage.

Prior-evidence warning: the answerability gate proved high-precision/low-recall
and stayed off; both LLM query-planning attempts (decomposition, packaging) were
negative. CRAG's facet checker is the next LLM-judgment-in-the-loop attempt and
must clear the same bar (judged on changed-context rows) before defaulting on.

---

## 6. Traceability deltas

Additive only (React trace viewer reads the current schema):

- `profile`: policy name.
- `model_choice`: `{model, reason}`. Keep flat `generator_model` as a duplicate
  key for one release before removal.
- `evidence`: `{verdict, method, missing_facets}`.
- `corrective_retrieval`: `{enabled, fired, added_chunks}`.
- API schemas must expose the same additions:
  - `app.api.routes_retrieval.TraceRecord` gains `profile`, `model_choice`,
    `evidence`, and `corrective_retrieval` so `/retrieval/inspect` does not
    drop them while materializing `TraceRecord(**trace_record)`.
  - `app.api.routes_config.ConfigView` gains `profile` plus the secret-free
    `policy_overrides`/`env_ignored` summary from `resolve_policy()`.
- Eval artifacts must record the actual generator, not only the configured
  default:
  - each eval row gains `profile`, `generator_model`, `model_choice`, and
    `model_choice_reason`;
  - eval `meta.json.active_config` gains the resolved policy summary;
  - cascade reports compute cost/latency from the per-row `model_choice`, not
    from `settings.llm_model`.

Everything else (intent decision, strategy+knobs, chunk lists per stage, per-stage
latency, citations) already exists.

---

## 7. Non-goals

- **Async — deferred (decision 2026-07-08).** The pipeline stays synchronous.
  Stage functions are already the right shape for later `asyncio.to_thread`
  wrapping. When revisited, order of attack: eval-runner rows (biggest wall-clock
  win, but only with `reranker_backend=minilm|qwen3` — the Bedrock rerank 2 req/min
  quota makes parallel rows pointless on that backend), RAGAS judge calls,
  corrective/subquery fan-out, rate-limit-aware cloud generate calls.
- Retry/regenerate cascading (see §4).
- Full CRAG implementation (PR5+, evidence-gated).
- Reviving query decomposition (two negative A/Bs; only candidate revival is
  inside a future fact_pattern_or_advice lane per the router-program backlog).

---

## 8. Migration plan (PR slicing)

**PR1 — pipeline extraction (minimal, no behavior change). FROZEN 2026-07-09.**
`app/pipeline/{state,stages,runner}.py`; move `run_answer`/`_run_pipeline` logic
into stage functions with straight-line control flow (no `try/finally`; see §2);
`answer_service.py` becomes a re-export shim (4 callers unchanged: CLI,
`routes_query`, `routes_retrieval`, eval runner). `state.py` holds `AnswerState`
only. Resist bundling anything else.

Prereq (Phase 0): commit the in-flight trace-text feature (full chunk `text` +
`_snapshot_results`) first so the golden baseline is captured against a clean
tree.

Verification — two layers:

1. **Live golden check** — `scripts/golden_pipeline_check.py`, capture/compare
   modes, requires live Qdrant + Ollama, run on the same index before/after.
   Compares normalized `(response, trace_record)` pairs; ignored/normalized
   fields: `trace_id`, `timestamp`, `latency_ms`, `stages[*].ms`. Question set:
   a hand-picked manifest with branch labels — `greeting`, `normal_answer`,
   `current_law_router`, `hard_abstain_min_chunks` (a question known to return
   zero/low corpus hits), `soft_abstain`, `session_rewrite` — path coverage over
   row count. Catches broad behavior drift.
2. **Unit regressions lock branch semantics** (deterministic, no live deps):
   - LLM error path: `generate` patched to raise `LLMError` → error response,
     no trace anomaly. (Not in the live golden script.)
   - Greeting path: still appends the session turn, attaches debug stages, logs
     completion, and writes/returns the trace exactly as today.
   - Exception semantics: a stage raising `RuntimeError` propagates and leaves
     no session turn and no trace record (today's behavior — only `LLMError` is
     converted to an error response).
   - Hard abstain via min-chunks (unit-level, alongside the live-golden row).

Test migration (in scope for PR1):

- `test_answer_service_router.py` / `test_strategy.py` monkeypatch
  `answer_service._run_pipeline` / `TraceWriter` / `STRATEGIES` — repoint the
  patches at `app.pipeline` (a re-export shim would not intercept them).
- `test_answer_service_sources.py` imports the private helpers `_chunk_trace` /
  `_cited_sources` directly — move those tests to the new module rather than
  re-exporting privates.
- `test_import_boundaries.py`: add the new invariant — `app.pipeline` may import
  `app.retriever.*`; `app.retriever` must never import `app.pipeline` (keeps
  "retriever demotes to a library" enforced, ADR-019 pattern). API modules keep
  importing the `app.retriever.answer_service` shim.

**PR2 — config profiles.** `policy.py`, `AnswerPolicy`, registry, `RAGLAB_PROFILE`,
`from_settings()` fallback. Stages read `policy.*` for behavior knobs. Trace +
`config_view` gain profile and the precedence summary; `routes_config.ConfigView`
is updated in the same PR. Verify `local` is bit-identical to PR1 and add
field-precedence tests for behavior-vs-infra env conflicts.

**PR3 — ModelRouter + cascade experiments.** `model_router.py`, `route_model`
stage, `cascade` + `local-cascade` profiles. Run §4a (cloud A/B) and §4b (local
router benchmark + local strong-generator benchmark). Graduation per profile on
its own evidence; negative results logged. Eval rows/meta and trace records carry
actual `model_choice` so mixed-model cascade runs are auditable.

**PR4 — evidence stage + corrective slot.** `EvidenceReport`, `gate_evidence`
subsuming min-chunks + answerability, `corrective_retrieve` no-op, trace fields.
`routes_retrieval.TraceRecord` is updated in the same PR. No behavior change
under any current profile.

**PR5+ — CRAG proper.** Facet-checker evaluator, targeted re-retrieval,
`crag-experimental` profile, judged A/B before any default flip.

---

## 9. Risks and tradeoffs

- **Refactor churn vs. locked baselines** (top risk). Eval history is keyed to
  flag configurations; PR1/PR2 must be provably no-op (golden tests) or every
  future A/B has a confounded baseline.
- **Over-abstraction** — the review checklist's named failure mode. Mitigation:
  stage = function, profiles = dict of frozen dataclasses, no registries/ABCs.
- **Dual config source of truth.** Env + profile can disagree; the precedence rule
  lives in one place and is surfaced in `show-config` + trace.
- **Trace schema breaks the frontend viewer.** Additive-only; duplicate renamed
  keys for one release.
- **Cascade cost/latency control.** Intent escalation is bounded (~2/5 lanes);
  `escalate_on_partial_evidence` later compounds with corrective retrieval — cap
  at one corrective round + one escalation, both traced. Local cascade adds
  latency (second local model swap-in) — measure wall-clock in §4b.
- **Local router false fires.** A weaker local classifier that over-escalates
  makes every easy question slow. §4b's zero-false-escalation metric gates this.
- **CRAG may not pay.** Three LLM-in-the-loop features failed A/Bs here. The
  refactor is justified independently (answer_service is at its complexity
  ceiling); CRAG itself stays gated on evidence.
