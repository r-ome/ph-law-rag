# Evaluation Methodology

Status: adopted 2026-07-11. Build items (§6) planned, not yet implemented.

Core principle:

> **Hashes prevent silent changes; source-change reviews authorize necessary ones.**
> A benchmark should be mechanically stable, but not frozen into legal obsolescence.

---

## 1. ELI5 (oversimplified)

The eval dataset is an exam we give the system to check if it's getting better or worse.

- **Frozen benchmark (81 questions)** — the official exam. Nobody is allowed to change
  the questions or the answer key, because we compare this month's grade against last
  month's. Changing the exam would make old grades meaningless. A tamper-seal (hashes)
  breaks the test suite if anyone edits a question quietly.
- **Dev set (50 questions)** — practice problems. We're allowed to look at the answers,
  study why we got them wrong, and tune the system against them. Because we tune
  against them, scores here are a little flattering.
- **Holdout (30 questions)** — the sealed final exam. We only ever look at the total
  score, never at individual answers. If we peek at one, that question is "burned" —
  it becomes a practice problem and gets replaced. This is the only honest measure of
  whether the system *generalizes* instead of just memorizing the practice set.

Two things can quietly ruin the exam:

1. **The answer key rots.** Laws get amended. A question rewarding last year's penalty
   is now a bug in the exam, not the system. So when a new law is ingested, every
   question touching the law it amends must be re-checked.
2. **Answers leak.** Every time we learn something from the sealed exam and act on it,
   the exam gets a little less sealed. Peeking at rows burns them; even watching the
   total score too often slowly turns the final exam into another practice set.

One more trap: the **grader is noisy**. The LLM judge can give the same answer a
different score on different days. So a small score movement means nothing until we've
measured how much the grader wobbles on its own.

---

## 2. Plain English

### The three splits

| split | rows | may inspect? | may edit? | purpose |
|---|---:|---|---|---|
| regression (frozen benchmark) | 81 | yes | only via controlled revision | longitudinal scoreboard — compare runs over time |
| dev | 50 | yes, freely | yes, freely | diagnose failures, tune the pipeline |
| holdout | 30 | **no** (aggregate only) | only pre-exposure, with sign-off | test generalization; write-once |

Terminology note: the 81-row split is a **frozen benchmark** (a hash-locked standing
scoreboard with a representative mix), not a classic "known-bug regression suite."
It *contains* some targeted regression cases but is not defined by them.

### Why frozen matters

Comparing today's score to an old one is only valid if both runs answered the exact
same questions against the exact same answer key. Each of the 81 rows has a SHA-256
hash in `data/eval_dataset.v1.sha256`; a unit test recomputes them and fails on any
drift. "Frozen" does not mean "never editable": when a ground truth is factually wrong
(precedent: eval_029's 24-hectare rule, capped at 12 by the Constitution), it is fixed
through a controlled revision — explicit sign-off, hash line recomputed, change
recorded in commit + devlog. Hashes prevent *silent* edits; the review process
authorizes *necessary* ones.

### Why the holdout is aggregate-only

The holdout's value is exactly the amount of information about it that has **not**
flowed into development. Leakage channels:

- **Row-level:** inspecting a row's output and fixing what you learned means the row
  now measures a fix made *for it*. The row is burned — reclassified as dev, replaced.
- **Aggregate-level:** running the holdout after every tweak and keeping tweaks that
  raise the score tunes against it indirectly. Run it rarely (milestones, not
  per-experiment) and log every aggregate read.
- **Authoring-side:** a holdout question that is a near-duplicate of a tuned dev
  question was effectively already seen. Dedup across splits — semantic near-dupes,
  shared templates, same-seed variants.

The runner enforces redaction (`holdout_redacted` in eval_store; aggregate summaries
only).

### Why composition ≠ performance

"3.7% ambiguous" describes the dataset's mix, not the system's skill, and not
production prevalence (there is no production traffic). There is no correct category
percentage. The mix must give each slice you make claims about enough rows to read a
signal over judge noise — roughly n ≳ 10; below that, slice movement is anecdote.
That is why the 2026-07 expansion over-weighted paraphrase and synthesis (the
discriminating axes) rather than mirroring any imagined traffic distribution.

Known thin slices: ambiguous (6, regression-only), OOS (12, regression-only),
synthesis in dev/holdout (6/4). These support case-level tripwires, not
generalization claims. The OOS moat number in particular is a regression check
("did the known fence hold"), not evidence of generalized abstention skill.

### Why ground-truth rot is a first-class risk

A row can become wrong while the question, retrieval, and model behavior are all
unchanged — an amendment changes the true answer. This has happened three times
(eval_029 post-freeze; eval_136/140 the same day they were written, when BP 195 was
ingested). Lesson: **ingesting an amending act is never additive-only** — it requires
a review pass over eval rows citing the amended base.

### Why judge noise dominates statistics

The RAGAS judge can swing faithfulness ±0.25 on byte-identical inputs. Confidence
intervals around an unstable measurement don't make it trustworthy. Priority order:

1. Stabilize generation (deterministic settings — temperature in Ollama `options`).
2. Stabilize/characterize judging (row cache; repeatability panel, §6.2).
3. Paired comparisons on identical questions; judge only changed-context rows.
4. Only then worry about sampling inference.

---

## 3. Project policy (the ten points)

1. Expand a category only when it becomes a **declared tuning target**. Size new dev/
   holdout rows by the distinct failure modes the corpus supports, the minimum
   improvement worth detecting, judge noise, authoring cost, and API budget — never a
   per-category quota. Forcing a quota past the corpus's genuine variety manufactures
   the near-duplicates the dedup rule forbids.
2. Declare the **minimum useful improvement and guard conditions before** running the
   experiment (prevents retrospective storytelling).
3. Compare **identical questions** old-vs-new; report paired case transitions
   (categorical) or per-row metric deltas (graded).
4. Define **expected behavior per row** when a capability admits different correct
   responses (clarify vs. abstain vs. answer — see §6.4).
5. **Cache unchanged judgments**; adjudicate (human review) borderline or
   decision-changing changed rows.
6. Report every slice **with its n**; treat small slices as case-level evidence.
   Always include the worst slice, with its size attached.
7. Preserve the frozen benchmark through **hashes, immutable versions, provenance,
   and signed-off edits**. Results always identify the exact dataset, corpus, git
   commit, pipeline config, generator, and judge versions.
8. Use **`expected_sources` to trigger ground-truth review** when legal sources change
   (§6.1).
9. **Burn exposed holdout rows** (and semantically related rows whose future treatment
   was informed by the exposure); preserve historical snapshots; version every
   replacement (§6.5).
10. **Defer production weighting** until real production prevalence exists. Never
    present eval-set proportions as user-traffic prevalence.

---

## 4. Experiment-report conventions

State **before execution**: the intended capability change; the minimum result that
justifies keeping it; guard conditions that must not regress; which rows/slices are
expected to change.

Example declaration:

> The router change is worthwhile only if it fixes at least three targeted ambiguity
> failures, introduces no new factual-routing regressions, and does not worsen median
> latency by more than 10%.

Report by metric type.

**Categorical outcomes — paired transition table:**

| transition | meaning |
|---|---|
| wrong → correct | fix on this test case |
| correct → wrong | regression on this test case |
| correct → correct | stable success |
| wrong → wrong | remaining failure |

"Wrong → correct" is a case-level claim. A generalization claim requires consistent
paired evidence on an untouched holdout.

**Graded RAGAS metrics:** paired per-row deltas alongside the aggregate change;
interpret against the judge's measured repeatability (§6.2). Use a binary transition
table only after declaring a pass/fail threshold in advance.

**Every slice with n**, worst slice always shown:

```text
Ambiguous: 4/6
Out-of-scope: 10/12
Synthesis: mean +0.06 across n=18
Worst slice: ambiguous, 4/6
```

**Guard metrics** (check the non-target slices): faithfulness, context precision,
factual recall, OOS abstention, latency, cost — plus capability-specific guards
(e.g., clarification frequency, so "always ask for clarification" can't game an
ambiguity score).

Standing method rules that remain in force: metric-targeted experiments are
router-isolated (declare eligible lane + target failure class; hold everything else
constant; primary read = eligible rows); judge only rows whose retrieved context
actually changed.

---

## 5. Technical reference

### Dataset mechanics

- File: `data/eval_dataset.jsonl` — 161 rows, one JSON object per line.
  Splits: 81 regression / 50 dev / 30 holdout.
  Categories: factual 85, paraphrase 36, synthesis 22, out-of-scope 12, ambiguous 6.
  OOS and ambiguous exist **only** in regression — abstention behavior is guarded
  there alone.
- Freeze enforcement: `data/eval_dataset.v1.sha256` holds one hash line per
  regression row; recomputed via the migration script's `_frozen_hash`; a unit test
  fails on mismatch. Holdout rows are **not** hashed (protected by secrecy, not
  immutability).
- Controlled-revision precedent (eval_029, commit 05d3f4c): statute-scope the
  question, expand GT to the corpus text, recompute the hash line, record sign-off in
  devlog + commit. The RAGAS row cache misses on the edited row next run — expected.
- Holdout pre-exposure edits (eval_136/140, commit bdf2b38): permitted only because
  **no holdout run had occurred** — the rows had influenced nothing. Post-exposure,
  the same defect would instead burn the rows.

### Run provenance (current practice)

Eval artifacts are named and pinned, e.g.
`gemma4-e4b_qwen06-baseline-131_20260711_104509` at git `bdf2b38`, recording
generator, embedder/collection, reranker, row count, and judge backend. Standing
baseline: 131 non-holdout rows — faithfulness .900 / relevancy .770 / precision .687 /
recall .833, abstention 123/131.

### Noise controls (current practice)

- Deterministic generation (temperature via Ollama `options`).
- RAGAS row cache — unchanged (contexts, answer) rows are not re-judged.
- Changed-context filter — after an intervention, judge/read only rows whose retrieved
  context changed; bit-identical rows are cache hits and carry no new information.
- Cross-judge agreement established 2026-07-10 (Anthropic Haiku vs GPT-5-mini judges
  agree); **within-judge repeatability not yet measured** — see §6.2.
- Known judge instability: faithfulness observed swinging ±0.25 on identical inputs
  (2026-06 observation; the panel will replace this folklore number with a measured
  floor).

---

## 6. Build items (planned, in leverage order)

### 6.1 Source-change → eval-row review trigger

Highest-value item. Mechanizes the ground-truth-rot lesson.

At sync time, when a source's content hash changes (or a new source is ingested):

1. Resolve the changed source to canonical source IDs.
2. Walk the manifest's forward-only **amendment edges** to the base authorities it
   amends — the trigger follows the *legal dependency*, not the filename. (BP 195 →
   edge to `anti_graft` → rows citing `anti_graft` would have caught eval_136/140.)
3. Match changed + amended-base IDs against every row's `expected_sources`.
4. Emit a warning report; affected rows require ground-truth review before the next
   trusted benchmark run.

```text
Source change detected: anti_graft_amendments_1982
Amends (via manifest edges): anti_graft
Affected eval rows: eval_132, eval_136, eval_140, eval_141, ...
Reason: new source ingested
Status: ground-truth review required
```

A warning report suffices at this scale — no workflow system. No universal per-row
`valid_as_of` field: the manifest stays the temporal source of truth; add row-level
dates only where the expected answer is explicitly time-dependent.

### 6.2 Judge-repeatability panel (cache bypass)

Turns the ±0.25 folklore into a measured noise floor per metric.

- Fixed calibration panel: ~8–15 rows spanning strong / weak / borderline responses.
- Freeze everything: generation outputs, contexts, judge model + prompt + settings,
  RAGAS version. **Bypass the row cache** for this command only (the cache exists
  precisely to prevent re-judging identical inputs; the panel needs the opposite).
- Judge each row ~5 times; report per-metric median / p90 / max within-row range.

```text
Faithfulness repeatability panel — rows: 10, repeats: 5
Median within-row range: 0.08
90th-percentile range:  0.17
Maximum observed range: 0.24
```

Interpretation rule this buys:

> Observed mean improvement 0.04 < measured judge variation for this metric → not
> treated as meaningful.

Rerun the panel only when the judge model, prompt, settings, or RAGAS version change.
**Caveat (keep verbatim): repeated judging measures variance, not correctness — a
consistently wrong judge still looks stable.** Human adjudication remains appropriate
when a borderline row changes an experiment's verdict. Cost is trivial on Haiku
(~50–75 judge calls).

### 6.3 Experiment-report conventions

§4 above. Documentation standard only — zero code. Applies to future devlog A/B
entries.

### 6.4 Expected-behavior labels for ambiguous rows

**Deferred until ambiguity becomes a declared tuning target.** Then label each
ambiguous row with the desired behavior, not whether current output passes:

```yaml
expected_behavior: clarify   # clarify | answer | conditional_answer | abstain
reason: jurisdiction is not specified and retrieved context does not resolve it
```

This stops an "always clarify" router from scoring well merely because the category is
named ambiguous. If ambiguity handling is ever tuned, the eval expansion (fresh dev
rows sized per distinct ambiguity modes — unclear jurisdiction, unclear effective
date, unclear party, conflicting authority, ambiguity resolved by retrieved text, … —
plus a blind holdout slice) must **precede** the pipeline work, or the tuned behavior
gets baked into the ground truths.

### 6.5 Holdout version labels

Convention decided now; needed only after the first exposure.

Every holdout aggregate identifies: holdout version, row count, manifest/dataset hash,
exposure state.

```text
holdout_v1  n=30  manifest_sha256=...  status=locked
```

After a row is inspected:

```text
holdout_v2  n=30
retired: eval_140          # burned row — historical v1 result unchanged
replacement: eval_162
exposure_source: experiment_router_2026_08
```

Rules: preserve the original result in the historical report; never retroactively
remove the row or recompute the old denominator; burn semantic relatives whose future
treatment was informed by the exposure (not automatically every row citing the same
statute); log every aggregate read — repeated score-watching weakens independence even
with no rows opened. If aggregate results repeatedly influence development decisions,
treat that holdout version as development data and create fresh validation evidence
before the next generalization claim.

---

## 7. Deliberately deferred

Not wrong — just not the highest-leverage controls at this project's scale:

- Fixed per-category dev/holdout quotas (20–30 / 30–50 rows per category).
- Preemptive expansion of every category.
- McNemar / formal significance testing (defer, don't reject — useful later for a
  large, genuinely binary behavior eval; it also only uses changed-outcome rows, so
  it has little power at current slice sizes).
- Production-weighted evaluation (no production traffic exists; never present the
  eval mix as traffic prevalence).
- Full-holdout retirement after inspecting one row (per-row burn is strictly cheaper
  when authoring is the expensive step).
- Confidence-interval reporting that ignores judge instability.
