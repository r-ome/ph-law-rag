# Phase 5 — Corrective Retrieval With Global Rerank

**Status:** CP1–CP2 complete and committed (`0f6bf79`); CP3 preconditions are
implemented and documented, pending their required commit before the sealed
retrieval-only run. This revision also numerically predeclares the CP3
context-size and CP4 context-recall gates and tightens the fired-row
target-preservation gate.

**Pre-result CP2 correction (2026-07-18):** the experimental profile initially
inherited `_base_profile`'s pinned `RetrievalKnobs`, which silently set sibling
expansion off while the Phase 4 control used the settings-derived local policy
with it on. That created an undeclared seventh delta and a deterministic CP1
cache miss. The profile now derives from `AnswerPolicy.from_settings()` and
applies only the six declared evidence/corrective deltas. This is a CP2
implementation correction, not a new arm or retune; it lands before any sealed
CP3 result. `crag-experimental` remains pinned for CP1-era reproducibility.

> Standalone working copy of the approved Phase 5 plan. The reconciliation
> rationale in the `# Phase 5 plan: Corrective retrieval with global rerank
> (2026-07-17)` section of
> [`retrieval_strategy_review.md`](retrieval_strategy_review.md) (committed
> `2152efb`) remains authoritative for the *approved design*. For *checkpoint
> results and gate definitions* this file is currently ahead — that section
> still reads "implementation not started" and has not absorbed CP1/CP2.
> Back-filling it (or amending its authority claim) is a **CP3 precondition**
> (see precondition 4); until that lands, this file wins on checkpoint state
> and gates.

**Thesis:** corrective retrieval works as *candidate discovery* feeding one
global rerank plus Phase 4 adaptive packaging, not as a context append. PR5c's
flat-to-negative result indicted the append packaging; Phase 5 re-tests the
facet checker with that packaging replaced.

## Reconciliation with pipeline-refactor PR5 (signed off 2026-07-17)

- **Facet checker survives** as the corrective trigger and source of
  `missing_facets`.
- **Per-facet targeted retrieval survives** as candidate discovery, redirected
  into the union pool instead of an append.
- **Additive append packaging retired** (margin filter, post-gate append,
  `corrective_max_added`). Never rerun.
- **PR5 §10.1's additive invariant reversed with sign-off:** corrective
  candidates may displace baseline chunks after the global rerank — that is
  the point of the redesign. The no-new-abstain guarantee is preserved:
  `min_chunks` runs first and unchanged; the checker returns only
  `sufficient` or `partial` and fails open.
- PR5c's negative result stands as evidence against the append packaging, not
  against the checker — the two were never separated in that A/B.

## Design (two-pass selection, one corrective round max)

```text
pass 1 (unchanged serving path):
  fusion pool -> rerank -> edge/parent/sibling expansion -> dedup -> adaptive select
facet check (Haiku, cached, fail-open) on the pass-1 adaptive-selected context
if verdict == partial:
  per missing facet (<= corrective_max_facets): targeted hybrid retrieval,
    top corrective_facet_reserve_n per facet in hybrid fused (RRF) order only
  union = pass-1 pre-rerank fused pool + facet candidates, dedup by chunk_id
pass 2: rerank the union ONCE against the original question
  -> serving selection tail verbatim (expansion -> dedup_results -> adaptive
     select, Phase 4 v2 caps 7/11/11/2400)
generate over the pass-2 context
```

Six binding design decisions:

1. **Checker input = pass-1 adaptive-selected context** (what generation
   sees), deviating from PR5's `pre_expansion`. Owned consequence: `partial`
   conflates retrieval misses with packaging drops — acceptable because pass 2
   unions the full pre-rerank pool.
2. **Union base = pass-1 pre-rerank fused pool** (schema-1.1 canonical pool;
   `SelectionResult.retrieved`).
3. **Per-facet retrieval does not rerank.** Fused (RRF) order only, top
   `corrective_facet_reserve_n` per facet. The sole rerank in the corrective
   path is pass 2 against the original question.
4. **Pass 2 reuses the serving selection code path verbatim**
   (`select_post_rerank`) — no bespoke merge, no round-robin, no margin
   filter.
5. **Union dedup is two-stage, matching the committed implementation**
   (resolved 2026-07-18; earlier copies wrongly claimed full `dedup_results`
   semantics on the union). *Pre-rerank:* the union is deduped by
   **duplicate chunk ID only** (`app/pipeline/corrective.py`). *Post-rerank:*
   full `dedup_results` semantics — explicitly represented merged chunks and
   fuzzy same-provision similarity (`app/retriever/dedup.py`; not "exact
   normalized text") — run in the verbatim serving tail, exactly as in
   serving. **Owned consequence:** near-duplicate facet candidates can
   occupy MiniLM rerank slots and displace unique evidence before the final
   dedup. Accepted for CP3 as-committed; if CP3 shows displaced targets
   traceable to duplicate rerank slots, pre-rerank full dedup becomes a new
   arm, not a silent change. CP3 precondition: a test asserting the actual
   **reranker input** as the **exact ordered sequence** (pass-1 pool ∪
   facet candidates, chunk-ID-deduped, nothing removed beyond that) — a
   set-of-IDs comparison is insufficient: it cannot catch reordered
   candidates, and the test must also verify that a duplicate-ID collision
   retains the **pass-1 pool instance**, not the facet copy; both affect
   deterministic reranking. Not only the final selected output. No
   provision-family collapse; **sibling-expanded leaves survive** — dedup
   exempts only results carrying `expanded_from_sibling`
   (`app/retriever/dedup.py`); a directly retrieved distinct leaf of the
   same provision can still be removed by fuzzy similarity, and broadening
   that exemption would be a deliberate implementation change with its own
   tests, not a wording fix.

   The reranker-input test lands with the CP3 precondition-2 batch.
6. **`sufficient` rows skip pass 2** and are identical to control by
   construction. Phase 4's adaptive caps bound the blast radius on fired rows.

## Knobs and comparator contract

Eval arm = profile `corrective-global-rerank-experimental`
(`crag-experimental` stays on legacy append so CP1 artifacts remain
reproducible).

| Knob | Candidate | Control-normalized | Notes |
|---|---|---|---|
| `evidence_gate` | `crag` | `min_chunks` | Existing field |
| `corrective_retrieval_enabled` | `true` | `false` | Existing field |
| `corrective_mode` | `global_rerank` | `append` (legacy backfill) | New |
| `evidence_judge_model` | `claude-haiku-4-5` | control-recorded (inert under `min_chunks`) | Existing PR5 field |
| `corrective_max_facets` | `3` | `None` | New |
| `corrective_facet_reserve_n` | `5` | `None` — conservative; escalation to 8 = new sealed arm with its own declared delta, never a silent retune | New |

`_SELECTION_KEYS` / `BEHAVIOR_FIELDS` / `_LEGACY_CORRECTIVE_DEFAULTS` land
with the knobs (done in CP2). **CP3 comparator gate = set equality** on
exactly the six deltas above vs control `phase4-adaptive-context-v2-minilm`;
any undeclared or missing delta fails.

Guards (policy construction time): `global_rerank` requires
`adaptive_context_enabled=true` and rejects `subquery_packaging_enabled=true`
— fail loud, never degrade silently.

## Checkpoints (stop for user approval after each)

### CP1 — Facet-checker offline audit ✅ PASSED (2026-07-17/18)

Haiku checker over the 131 sealed pass-1 contexts via `raglab
eval-facet-audit`; content-addressed cache, fail-closed without
`--authorize-paid-calls`; 131 authorized calls made, cached permanently.

- Partial rate **26/131 = 19.85%** — gate [5%, 35%] **pass**.
- Hand-check **33/45 = 73.3% real gaps** — gate ≥50% **pass** (6 PRESENT
  judge-noise, 6 NOT_NEEDED over-broadening).
- Facet classification: 37 absent-from-pool / 4 dropped-by-selection / 4
  judge-noise — lift, if any, must come mostly from facet retrieval.
- Known false-sufficient row **eval_056 now caught** (names the Art. 1145
  gap); watch rows **eval_129/eval_124 `sufficient`** (no firing).
- 11 unparseable-output fallbacks: 8 = OOS moat rows (benign, arguably
  protective), 3 factual rows lose coverage (known limitation; raw judge
  output not frozen). (Corrected 2026-07-18: an earlier copy said 9 OOS,
  contradicting the 11 total; the artifact contains 8 OOS + 3 factual.)

Artifacts: `data/eval_results/runs/2026-07-17/phase5-cp1-facet-audit`, cache
`data/eval_results/facet_audit_cache/v1` (131 entries; replay validated
131/131 hits post-relocation).

### CP2 — Mechanism + plumbing, eval-only arm ✅ DONE (committed `0f6bf79`)

`corrective_mode="global_rerank"` implemented per the six decisions;
displaced-baseline count traced; checker calls in eval runs resolve through
the CP1 cache, misses fail closed naming the eval row; prompt contract +
cache primitives relocated to `app/retriever/facet_checker.py` (import
boundary), cache keys unchanged. Full unit suite 416 passed; shipping
profiles proven inert.

### CP3 — Retrieval-only sealed run ⏳ NEXT (blocked on preconditions below)

131 rows, MiniLM, frozen index, CP1 cache replay (zero paid calls; a cache
miss = pass-1 context drift ⇒ stop and report, never authorize calls to
paper over it). Seal write-once (suggested tag
`phase5-corrective-global-rerank-minilm`); publish comparison vs
`phase4-adaptive-context-v2-minilm`.

**Preconditions (2026-07-18 review — must land before the sealed run):**

1. **CP3-gate comparator.** The current comparator
   (`app/evals/retrieval_comparison.py`) reports only pre-rerank-pool and
   selected-context changes; the sealed record already carries
   selected-context, source-map, and prompt hashes
   (`app/evals/frozen_contexts.py`). Extend the comparator (or add a
   CP3-specific report step) to mechanically enforce every binding gate
   below — sufficient-row prompt/source identity, fired-row target-set
   preservation, expected firing population, context bounds — and emit
   pass/fail per gate in the published report. No gate may be checked by
   eyeball only. (Watch rows are reporting-only, not gates — see the
   non-gating reports block.)
2. **Automated rejection tests** for the new checks: each gate needs at
   least one unit test where a synthetic violation (displaced target,
   prompt-hash mismatch on a sufficient row, unexpected firing row,
   context-bound breach) is rejected. The target tests must include a
   **leaf-granularity case**: both arms retain the same `source_id` and
   `provision_id`, but the candidate loses the control's annotated
   `unit_label` — this must fail. A provision-only implementation would
   pass a generic displaced-target test while violating the
   `(source_id, provision_id, unit_label)` identity the gate declares.
3. **Target-set check uses final sets, not any-snapshot membership.** The
   existing target-presence helper (`app/evals/retrieval_runner.py`) scans
   both the pass-1 selected and final corrective snapshots and returns true
   on any one target, so it can mask a provision displaced by pass 2 (43
   rows have multiple targets; eval_124 has four). It must not be used for
   the fired-row gate.
4. **Authoritative-record back-fill.** Update the Phase 5 section of
   `retrieval_strategy_review.md` with CP1/CP2 results and these gate
   definitions (or amend its authority claim) — **including the decision-5
   design correction**: that section's canonical diagram and decision 5
   still require full `dedup_results` on the union *before* reranking,
   contradicting the resolved two-stage semantics above; both must be
   reconciled or the sealed arm differs from the authoritative approved
   design — **and add a concise Phase 5
   status/mechanism section to `docs/project_plan.md`** — it is the
   project's declared source of truth and its retrieval-architecture
   coverage currently ends at Phase 4, while CP1/CP2 added a new
   experimental retrieval mechanism. Commit all three documents — this plan
   file is currently untracked, and an uncommitted predeclaration is not a
   predeclaration. Must land *before* the sealed run, not at the CP3 result
   commit.
5. **External gate inputs are hash-bound to sealed artifacts, not mutable
   files.** The target gate and firing-population gate both reference
   records that live outside the frozen rows, so the comparator must:
   - recompute the ordered target hash from the loaded target sidecar
     (capture algorithm at `app/evals/retrieval_runner.py:342`) and require
     equality with **both** bundles' `targets_identity` — a mismatch =
     target drift ⇒ reject;
   - load the expected 26 partial-row IDs from the CP1 audit artifact only
     **after** validating its `rows_file_hash`, `summary_file_hash`, and
     `source_bundle_file_hash` (`app/evals/facet_audit.py:362`);
   - reject on target/audit drift — never trust current mutable labels,
     and never reduce the firing-population gate to `fired_count == 26`.
6. **Clean-worktree preflight for the sealed run.** Retrieval code identity
   currently omits behavior-critical files (`adaptive_context.py`,
   `dedup.py`, `facet_checker.py`, `policy.py` — see the list at
   `app/evals/retrieval_runner.py:167`), and `git_sha` does not capture
   dirty changes, so CP3 could seal against modified behavior without
   recording it. After the precondition commit: the sealed run requires a
   **clean worktree** (assert and record), or code identity is expanded to
   cover every Phase 5 behavior file with dirty-state provenance recorded
   in the bundle. Clean worktree is the default; the expansion is the
   fallback only if a dirty run is explicitly approved.

Binding gates:

- Comparator semantic delta set == the six declared deltas.
- `sufficient` rows: identical to control on **all five** sealed identity
  fields — `selected_context_hash`, `context_block_hash`, `source_map_hash`,
  `system_prompt_hash`, `user_prompt_hash` (evidence-block fields excluded
  by design). The comparator binds to these field names exactly.
- Fired rows — **target-set preservation:** per row, compute the exact set of
  matched expected targets from the **final `selected_results`** of each
  arm; gate = `control_targets ⊆ candidate_targets`. **Target identity =
  `(source_id, provision_id, unit_label)` when the annotation carries a
  `unit_label`** (five fired rows have canonical leaf targets — e.g. rows 41
  and 102 of `data/eval_retrieval_targets.jsonl`), so retaining a different
  chunk of the same provision while dropping the annotated leaf is a
  **fail**, not a pass; targets without `unit_label` match at
  `(source_id, provision_id)`; source-only annotations match per the
  existing semantics in `app/evals/retrieval_targets.py`. Any target the
  control had that the candidate's final selection lacks fails the run,
  regardless of what pass 1 or intermediate snapshots contained.
- **Context bounds (numeric, predeclared).** Pass 2 reuses the contract-v2
  selector verbatim, so caps 7/11/11/2400 are structural. The gate direction
  is *increase* (blast-radius), not Phase 4's 35% reduction ceiling (a
  reduction watch — inapplicable here).
  Context size is **not monotonic** under global rerank: pass 2 changes
  ranking, bundle order, stabilization, and cap-stop points, so a fired
  row's final context may shrink or grow. The bounds below are blast-radius
  guards on growth; no lower bound is set — instead, **signed per-row size
  deltas are reported** for every fired row. Scope caveat: the fired-row
  target-set gate catches only **annotated-target loss**. A shrink can still
  drop a qualifying leaf, exception, comparison chunk, or unannotated
  supporting provision while the expected provision ID survives; that
  residual semantic-shrink risk is deferred to CP4's recall and
  faithfulness gates, not gated at CP3.
  **Binding metric field:** the **final-pass** `adaptive_context`
  diagnostic's `rendered_tokens`, read through a **normalized accessor**
  that handles both artifact shapes:
  - *derived Phase 4 control bundle:* top-level `adaptive_context`
    diagnostics (it has zero adaptive trace stages — a bare "last trace
    stage" rule would reject every control row);
  - *direct captures* (`retrieval_trace.stages`): require **exactly one**
    adaptive stage on non-fired rows and **exactly two** on fired rows,
    selecting the **second** — one stage on a fired row is a rejection, not
    a fallback, because accepting it could silently read pass 1;
  - anything else (zero stages on a direct row, >2, or a shape matching
    neither case) **fails, with a rejection test per case**. *Not* the retrieval
  summary's final-context token estimate, which sums per-candidate
  estimates and reports a different number (~1,149.5 control mean via
  `retrieval_metrics.py`). **p95 convention: nearest-rank, zero-based index
  ⌈0.95·n⌉−1** of the ascending-sorted list (matches the documented control
  value).
  Against control `phase4-adaptive-context-v2-minilm` (mean 1,372.1 / p95
  2,372 / max 2,696 rendered tokens; p95 = zero-based index ⌈0.95·n⌉−1 of
  the ascending-sorted list, the convention that reproduces 2,372) and the
  Phase 3 fixed-packaging envelope (mean 1,552.9 / p95 2,649 / max 3,274):
  - all-131-row mean ≤ control mean +10% (≤ 1,509.3) **and** ≤ the Phase 3
    mean 1,552.9;
  - p95 ≤ 2,649; maximum ≤ 3,274 (i.e., never worse than pre-adaptive
    fixed packaging on any of the three stats);
  - per-row: crossing the 2,400 soft token target happens only at an
    **admitted atomic boundary** — the actual selector contract
    (`app/retriever/adaptive_context.py`), which includes the mandatory
    floor and singleton-bundle admissions, not just multi-chunk sibling
    bundles (control sufficient rows eval_047/eval_109 already overflow via
    final singleton admissions and must stay identical by construction).
    This atomic-admission property is a **structural invariant, not a
    comparator gate**: it is guaranteed by the selector's own unit tests
    plus the clean-code provenance preflight (precondition 6), because the
    comparator cannot reconstruct admitted bundles from the sealed record.
    The only mechanical overflow gate is the numeric one below.
    Gate — **newly affected rows, not net count**:
    `len(candidate_overflow_ids − control_overflow_ids) ≤ 3` (a resolved
    control overflow must not buy headroom for a new one). Newly
    overflowing and resolved row IDs reported separately, each with its
    magnitude (per-row magnitude is already bounded by the max ≤ 3,274
    gate; a resolved overflow is a signed shrink on a fired row and shows
    up in the signed-delta report above).
- Expected firing population: exactly the 26 CP1 partial rows (a different
  set = drift ⇒ stop and report). (Category-slice reporting is non-gating —
  moved to the reports block below; CP3 defines no category statistic to
  gate on, and a binding gate without a mechanical criterion would violate
  precondition 1.)

Non-gating reports (reporting-only by design — watch rows carry no CP3
pass/fail criteria, so they do not belong in the mechanically enforced gate
list; their gated protection: eval_129/eval_124 were CP1 `sufficient` — no
firing — so it is the five-field sufficient-row identity gate, not the
target-set gate, which applies only to fired rows; for eval_058 it is the
CP4 leak gate):

- Watch rows eval_129 / eval_124 / eval_058 reported individually. eval_058
  is OOS with no retrieval targets and is a CP1 partial (all three facets
  absent-from-pool), so the target-set gate is vacuous for it — without an
  explicit watch its context change would go unexamined until generation.
- Displaced-baseline counts; which absent-from-pool facets were actually
  recovered; per-row corrective trace sanity.
- Per-category slices: selected target survival and parent-provision
  coverage deltas vs control, each reported with its n; slices with n < 10
  fired rows are case-level evidence — no directional claim.

### CP4 — Matched generation A/B (pending)

gemma4, deterministic generation, RAGAS row cache, judge changed-context rows
only.

**Precondition (must land before any generation/scoring): CP4 paired
harness.** The existing paired comparator cannot run this checkpoint: it
permits only `adaptive_context_enabled` as a policy difference and rejects
semantic packaging-pool changes Phase 5 intentionally causes
(`app/evals/paired_aggregate.py`), computes quality over *all*
common-answered rows rather than changed rows, and lacks the +0.05 recall
and row-level answer-leak gates. Extend it (or add a Phase 5 paired step)
with: exact acceptance of the six declared deltas; changed-row cohorting per
the definitions below; the declared gates implemented mechanically;
write-once report publication; and rejection tests per gate — including the
synthetic OOS abstain→answer transition, which belongs to *this* harness's
test batch (it cannot meaningfully land with the CP3 retrieval-comparator
tests).

Harness requirements beyond the gates (2026-07-18 review):

- **Delta validation opens the sealed retrieval bundles.** Generation
  bundles carry only the retrieval configuration *hash*, not the six
  values (`app/evals/generation_replay.py`), so generation metadata alone
  cannot prove the delta set. The harness must resolve each arm's
  referenced sealed retrieval bundle (verifying bundle hashes) and reuse
  the CP3 declared-delta comparison against it.
- **Matched generation and scoring identity enforced explicitly.**
  Retrieval-delta validation alone is insufficient: the bundles'
  generation configurations carry self-check, later-enacted preference,
  routing, and generator settings that can alter output. The harness must
  require equality of **all effective generation configuration values**
  between arms, excluding only the inert profile label; an identical
  generator model override; and full **RAGAS scoring identity** (judge
  backend/model, metric set, scorer version, row-cache namespace). Each
  mismatch class gets a rejection test.
- **Scoring cohort enforced before the scorer, not by cache luck.** The
  scorer processes every non-abstained result and skips judge calls only
  on incidental cache hits (`app/evals/ragas_scorer.py`). The harness
  passes only the changed-context subset to `score()` and asserts that no
  unchanged-context row reaches the scoring call.
- **Generation equivalence on unchanged-context rows is gated, not
  assumed.** There is **no generation cache**: both arms generate all 131
  rows (`replay_frozen()` runs for every uncaptured row,
  `app/evals/generation_replay.py`); only RAGAS *scoring* of
  prompt-identical rows can reuse the row cache. Deterministic settings
  make identical output *expected*, but generation can drift
  independently. The harness compares every unchanged-context row across
  arms on the **exact generation-record fields**: `answer`, `abstained`,
  `contexts`, `cited_sources`, `context_sources`, `generation_skipped` —
  and **stops the checkpoint** on any difference: with drift on unchanged
  rows, the paired result can no longer be attributed to the retrieval
  change. `selected_chunk_ids` is deliberately **excluded** — the plan
  permits selection-only differences with identical prompts.
- **Zero generation errors, gated before scoring — fail closed on the
  field itself.** The pipeline catches `LLMError` and returns an ordinary
  non-abstained answer with `error=True`
  (`app/pipeline/frozen_generation.py`), but the replay artifact does not
  persist `error` (`app/evals/generation_replay.py`) — so RAGAS would
  score the failure message as a real answer. Precondition: preserve
  `error` in generation records. Gate: **every generation row in both arms
  must contain `error` with type exactly `bool` and value `False`**;
  missing, `null`, non-boolean, or `true` all reject before scoring or
  report publication — never `bool(row.get("error"))`, which silently
  accepts legacy rows lacking the field. Consequence: the control needs a
  fresh compatible generation bundle, or an explicitly approved legacy
  adaptation. Synthetic rejection tests for each malformed shape.

Gates (numeric, predeclared 2026-07-18 — before any CP3 result exists, per
`eval_methodology.md` §3.2; not to be revisited after CP3):

- **Cohorts (two distinct sets — do not conflate):**
  - *Fired rows:* evidence verdict `partial` triggered pass 2
    (`corrective_ran=True`, `app/pipeline/corrective.py`) — expected the 26
    CP1 partial rows. A fired row can still end with an unchanged final
    selection.
  - *System-prompt identity gate (every row):* `system_prompt_hash` must be
    identical across arms on all 131 rows — corrective changes only the
    context, never the system prompt, so any difference = drift ⇒ stop.
    With that held, the user prompt is the complete varying generation
    input and can define the cohort alone.
  - *Changed rows:* **`user_prompt_hash` differs from control** — the hash
    of what generation actually sees. Not `selected_context_hash`: that
    hashes complete selected-result objects including rerank scores and
    metadata (`app/evals/frozen_contexts.py`), so a pass-2 score change
    could class a row as changed while the rendered context and prompt are
    identical. Rows where `selected_context_hash` differs but
    `user_prompt_hash` is identical are **selection-only changes** —
    reported separately, excluded from the quality cohort. Every changed
    row must be a fired row (a changed non-fired row = drift ⇒ stop).
    Unchanged rows are **prompt-identical** (not necessarily byte-identical
    in their retrieval records — a permitted selection-only row differs on
    `selected_context_hash`); they are still generated in both arms (no
    generation cache exists — only their RAGAS scoring reuses the row
    cache) and must pass the generation-equivalence check above.
- **Quality-delta cohort:** paired per-row deltas over **common-answered
  changed rows** (both arms answered — RAGAS excludes abstained rows,
  `app/evals/ragas_scorer.py`). Abstention transitions are handled by the
  separate row-level gate below, never folded into the metric means.
- **Context recall:** mean paired Δ on common-answered changed rows ≥
  **+0.05**. This is the predeclared **minimum useful effect size** (per
  `eval_methodology.md` §3.2), not a statistical noise bound — no
  repeatability-panel measurement of the context-recall noise band exists
  yet, so no noise-derivation claim is made. Below +0.05 ⇒ CP5 defaults to
  shelve. This is a **mechanism gate** (does correction help where it acts),
  a conditional effect — it does not by itself justify the per-query checker
  cost.
- **Exposure-weighted impact (report, feeds CP5):** all-query weighted
  recall Δ = (Σ paired Δ over common-answered changed rows) / 131, with
  prompt-identical rows contributing zero by construction (no additional
  judging). Ten changed rows at +0.05 is only ≈ +0.004 exposure-weighted —
  the CP5 cost decision must cite this number, not the conditional mean.
- **Faithfulness guard (non-inferiority at −0.01):** mean paired Δ on
  common-answered changed rows ≥ −0.01 (the established guard threshold
  from Phase 4's locked gates; a −0.01 decline passes, so this is a
  non-inferiority guard, not "flat-or-up").
- **Abstention and leaks (row-level gates, separate from the means, binding
  at every sample size):** abstention accuracy not down; **zero new false
  abstentions, defined set-based, not by aggregate count** — no in-scope
  row answered by control may abstain under the candidate (the evaluator
  exposes only aggregate counts in `app/evals/report.py`, under which one
  new false abstention can be hidden by another resolved one; requires a
  synthetic **offsetting-row rejection test**: one resolved + one new false
  abstention, net zero, must still fail); **zero new `answer_leaks`**,
  likewise set-based — no expected-abstain row that abstains under
  control may answer under the candidate. An OOS leak is excluded from the
  common-answered cohort and can be offset in aggregate accuracy, so only
  this row-level gate catches it. Requires a **synthetic rejection test**:
  an OOS abstain→answer transition must fail the gate (part of the CP4
  harness precondition's test batch).
- **Watch row eval_058** (OOS, no targets, fires as a CP1 partial —
  corrective retrieval on it can only add near-miss wage-adjacent chunks,
  the exact pressure that flips abstain to answer): abstain/answer outcome
  reported individually.
- Small-N rule — **aggregate RAGAS thresholds only**: common-answered
  changed n < 10 ⇒ the context-recall and faithfulness means become
  direction-only and the gate decision escalates to the user.
  **Direction-only is defined as:** report favorable iff mean context-recall
  Δ > 0 **and** mean faithfulness Δ ≥ 0; otherwise unfavorable. The label is
  deterministic and carries no pass/fail force — either way the decision
  escalates. **n = 0 is
  declared inconclusive**, not a pass: with no scorable changed rows the
  lift gate cannot be evaluated, and CP5 defaults to **shelve** unless a
  separately predeclared targeted evaluation is proposed and approved. The
  row-level abstention/leak gates above are evaluated over the full row set
  (a leak or false abstention is by definition *not* common-answered), so
  they remain binding at every sample size including n = 0 — but passing
  them cannot rescue an inconclusive arm.

Report-only: ambiguous relevancy (n=6) and synthesis relevancy soft spots
from Phase 4.

### CP5 — Graduation decision (pending)

Keep-or-shelve on CP3+CP4 evidence; ADR and any serving-default flip are the
user's call. If Phase 5 graduates, note the cost-model change: the checker
becomes one paid Haiku call per query in an otherwise-local retrieval loop —
and that cost is paid on *every* query, so the graduation case must weigh
the **exposure-weighted** recall impact from CP4's report, not the
conditional changed-row mean.

## Executor handoff notes

- **Stop after CP3.** Publish the CP3 comparison and report; CP4 requires
  explicit user approval before any generation or scoring.
- **CP4 RAGAS cache misses are paid calls.** The cache is global and
  content-addressed (question, answer, contexts, reference, scoring
  identity), so a changed-context row can still hit on an identical sample
  from an earlier run. After generation, compute the changed-row cache
  keys, report **actual** hits/misses and estimated cost, then obtain
  authorization before scoring.
- **Devlog is required output** for the comparator implementation and the
  CP3 result (path in project `CLAUDE.md`; it lives outside the repo, so
  writing it may need filesystem approval — ask, don't skip).

## Standing constraints / out of scope

- **Holdout stays sealed.** Read once for Phase 4 Stage B; any further access
  requires a separate predeclared plan and explicit user approval.
- Watch rows eval_129 (Section 11 family), eval_124 (Section 145), and
  eval_058 (OOS abstain under corrective pressure) carried through every
  checkpoint.
- Out of scope: re-checking the facet verdict after the corrective round, web
  retrieval, escalate-on-partial arms, heuristic gating of the checker call.
- Backlog (cosmetic): `state.corrective_max_added` is append-mode-only;
  `runner.py`'s `None`-fallback substitutes `subquery_reserve_n` misleadingly
  under `global_rerank`.
