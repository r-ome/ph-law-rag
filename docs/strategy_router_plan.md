# Strategy Router — Plan

**Status:** locked 2026-07-07. Planning only — nothing in this document is implemented yet, except the R1 step-0 labeling artifacts (labels, loader, test, config keys), committed 2026-07-07. R1 build constraints amended same day after external review (see R1 "Binding constraints").

## Motivation

Retrieval behavior today is a pile of loose booleans and knobs in `Settings` that compose implicitly. This plan replaces them with named, frozen **strategy presets**, then adds an LLM **intent classifier** that routes each query to one preset. Router-over-presets, not an agent loop: the model makes one bounded 1-of-N decision; all retrieval behavior lives in code/config, so every lane is reproducible and independently evaluable.

Grounding from this project's eval history:

- Two LLM-planning experiments (query decomposition, subquery packaging) produced negative results — the LLM *constructing* retrieval behavior over-splits and regresses recall. Classification is a far narrower task (the Haiku answerability gate fenced OOS 12/12).
- The fix-batch principle: deterministic beats instruction.
- The eventual payoff is the r/LawPH forum track (see Roadmap): fact-pattern questions are genuinely multi-issue, and the router gives a future multi-step "advice" strategy a contained lane to live in.

## Architecture

```
question
  -> greeting guard (existing is_conversational; call site relocated under router, not duplicated)
       -> greeting response, no retrieval
  -> if chat session: condense/rewrite to standalone question
  -> LLM classifier sees the standalone question
       -> {"intent": <one of 5>, "confidence": "high"|"low"}   (nothing else)
  -> low confidence or malformed JSON -> default
  -> static INTENT_TO_STRATEGY mapping (code-owned dict)
  -> preset resolves its owned knobs over env/settings
  -> deterministic retrieval -> generation
  -> answer + trace with intent, confidence, strategy name, resolved knob values
```

The classifier is a **separate LLM call** from the query rewriter. Same model may serve both, but distinct calls keep eval attribution clean: if retrieval worsens, we know whether the rewrite or the route caused it.

## Intents (v1)

| Intent | v1 status | Maps to |
|---|---|---|
| `default` | baseline strategy | `default` |
| `citation_lookup` | candidate preset — blocked on R3 BM25 trace check | `citation_precision` or `default` |
| `list_or_rule_synthesis` | label only (parent expansion + k30 are already defaults) | `default` |
| `amendment_or_current_law` | candidate preset — operative/consolidated controls, pending R3 | `current_law` or `default` |
| `out_of_scope` | label only | `default` |

Cut from the initial 9-class proposal: `case_law` (no corpus behind it — a lane pointing at nothing adds misclassification surface), `broad_research -> decomposition` (re-enables two proven failures), `procedure_or_remedy` / `statute_interpretation` / etc. (no evidence they need different retrieval; forum data may revive a procedure lane later).

## Locked rules

1. **No citation-regex pre-route.** The hand labels killed it: "Is rape still prosecuted under Article 335?" contains an explicit citation but is an amendment probe (as is the RA 11648 row); the four rows actually labeled `citation_lookup` contain no article/section citation. A strict regex either misroutes the hardest-won amendment fixes or never fires. The classifier owns `citation_lookup`. The only deterministic pre-route is the greeting guard.
2. **`out_of_scope` maps to default retrieval, not no-retrieval.** Gate history: 12/12 OOS fenced but ~11/70 in-scope questions false-fenced; the Haiku generator already leaks 0/12 with no gate. A no-retrieval lane buys latency and risks false abstains — the costliest error. OOS is a trace/eval label; the generator does the fencing. Revisit only if serving moves to a generator that leaks.
3. **If a preset does not change at least one retrieval/generation knob from `default`, it is a label, not a strategy.** No preset names that don't differ in behavior.
4. **Intent and strategy are distinct namespaces**, mapped many-to-one. A synthesis question traces as `intent=list_or_rule_synthesis, strategy=default` — that visibility is how a label earns promotion to a preset (eval shows default losing on that intent's rows).
5. **Precedence is explicit.** Presets own retrieval-behavior knobs: `dense_top_k`, `sparse_top_k`, `rerank_top_n`, `parent_expansion`, `prefer_operative`, consolidated/operative controls. `Settings`/env owns infrastructure: backends, URLs, model names, paths, logging, timeouts. For a routed query, preset-owned values override env — otherwise strategies aren't reproducible.
6. **The trace logs the resolved strategy on every query**: intent, confidence, strategy name, and final knob values. This is the guard against the stale-`.env` class of bug (`dense_top_k=10` silently ran the host at k10 after the k30 flip — caught twice).
7. **Rerank strategies tune depth (`rerank_top_n`), never call count.** Bedrock rerank quota is 2 req/min, non-adjustable.
8. **Generation is untouched** throughout this plan. The `default` lane stays byte-identical to today until an eval opens a preset.
9. **Multi-turn ordering:** condense to a standalone question first, then classify the standalone question (a follow-up like "and what's the penalty now?" isn't classifiable raw).

## Milestones

Refactor before router: strategies must be deterministic and testable before the classifier is wired in.

### R1 — Classifier accuracy A/B (offline, no runtime changes)

**Step 0:** commit the parked labeling artifacts: `data/eval_intent_labels.jsonl` (81 rows, question-keyed — the dataset has no `id` field, so positional keying would break on insert), `app/evals/intent_labels.py` loader, its unit test, and the `eval_intent_labels_path` config additions. Label distribution: default 38 / list_or_rule_synthesis 14 / amendment_or_current_law 13 / out_of_scope 12 / citation_lookup 4 — 31/81 (38%) route non-default.

**Build:**

1. Classifier prompt: input = standalone question; output = strict JSON `{"intent", "confidence"}`. Include 1–2 few-shot examples per intent drawn from **non-eval** questions (no benchmark contamination).
2. Standalone script (e.g. `scripts/classify_intent_ab.py`): loads the 81 labels, runs **three arms**, all deterministic — local mistral (temp 0), Haiku (temp 0), and a zero-shot NLI cross-encoder (one hypothesis sentence per intent, argmax over 5 scores; score margin = confidence). Writes per-arm artifacts: per-row prediction, accuracy, per-intent precision/recall, confusion matrix.
3. Malformed JSON counts as a miss (that is real router behavior: parse failure -> `default`). The cross-encoder arm has no parse-failure mode; its analog is a low top-score margin -> `default`.
4. Offline agreement stats across arms (a few lines, $0): does inter-arm disagreement predict misclassification? This prices — without building — the deferred ideas: verification layer, disagreement->default gating, cheap-first cascade (cross-encoder classifies, low margin escalates to mistral). None of these ship in v1; R1 data decides whether any is worth a v2.
5. Do **not** train anything on the 81 rows — they are the benchmark. Zero-shot only (prompt / hypotheses); fine-tuned classifiers would need separate training data.

**Binding constraints (review 2026-07-07):**

1. **Response cache is for crash/resume only, never prompt iteration.** Key = SHA-256 over (arm, exact model id, fully rendered prompt — few-shots and NLI hypotheses included — question). The NLI arm caches its raw 5 scores; the margin threshold is applied at scoring time, outside the key. A cheap rerun loop against the 81 rows is a benchmark-contamination vector regardless of key correctness.
2. **Prompt and hypotheses are authored once**, smoke-tested only on a small non-eval question pool (~5), then benchmarked once. Any post-results prompt change is a v2, recorded in `meta.json`.
3. **`metrics_<arm>.json` reports per-intent precision/recall/support on routed predictions** (post confidence-gate/parse-failure → `default` — the behavior being gated), with raw metrics as a secondary block. Confusion matrix = gold × routed.
4. **The NLI margin threshold is pre-registered** in `meta.json` before any benchmark scoring — calibrated off-benchmark or fixed a priori (e.g. top-2 softmax margin < 0.15 → `default`). Entailment index read from `model.config.label2id`, never hard-coded. No threshold sweep over the benchmark.
5. **Artifacts use the bundled layout** `data/eval_results/runs/YYYY-MM-DD/intent_ab_<ts>/` but do **not** write RAGAS `latest.json` or `manifest.jsonl` (those carry RAGAS-run semantics). Files: `predictions_<arm>.jsonl`, `metrics_<arm>.json`, `confusion_<arm>.csv`, `agreement.json`, `meta.json` (model ids, prompt hash, few-shot source, frozen threshold).

**Done when:** all three arms scored; confusion matrices produced; every `amendment_or_current_law` confusion read row-by-row (the dangerous distinction is amendment/current-law vs history-flavored questions); agreement stats computed.

**Decision gate:** cheapest arm within ~2–3 rows of the best arm serves the router, where cost order is cross-encoder (~50ms, $0) < mistral (~1–2s, $0) < Haiku (cloud). The classifier runs on every query before retrieval, so the latency column is a serving cost, not just an eval detail. Milestone cost: pennies (Haiku arm only).

### R2 — Strategy preset layer (refactor, zero behavior change)

**Build:**

1. Strategy module: `Strategy` interface = "standalone question in -> contexts out". The v1 implementation is a knob-bundle dataclass, but the interface leaves room for a future multi-step strategy (the forum/advice lane) without reshaping the router.
2. Registry: `default` plus unwired stubs for `citation_precision` and `current_law` — registered only if/when their R3 trace checks pass.
3. Precedence per locked rule 5; resolution happens in one place.
4. Trace gains the resolved-strategy block (rule 6).
5. Tests, including the zero-behavior-change proof: the `default` preset resolves to exactly today's effective settings.

**Binding build notes (review 2026-07-07):**

1. **`RetrievalKnobs` owns seven fields:** `dense_top_k`, `sparse_top_k`, `rerank_top_n`, `parent_expansion_enabled`, `prefer_operative_enabled`, `retrieval_operative_only`, `consolidated_dedup_enabled`. The last two are rule 5's "consolidated/operative controls" and are exactly what an R3 `current_law` preset would vary. `sparse_overfetch_k`, `parent_expansion_min_children/max_chars`, and `edge_expansion_enabled` stay Settings-owned (tuning constants / not named by rule 5; promote later if a preset needs them).
2. **`default` resolves by pass-through to `Settings`** — the Settings-backed baseline lane. Pinned code values would break the zero-change proof and the env-sweep eval workflow; pinning is for non-default presets after R3 proves a diff.
3. **Thread knobs through every rerank/retrieval path, including the leaks:** `expand_with_edges` re-calls `rerank()` (reads `settings.rerank_top_n`; edge expansion is on by default) and `packaged_retrieve` reads it too — both take knobs even though subquery packaging is off. `retrieval_operative_only` flows into **both** dense and sparse paths; passing it into the operative filter in `app/indexing/vector_store.py` as a `bool | None = None` parameter is fine despite the package boundary.
4. **Single-resolver rule:** routed strategy execution never reads preset-owned settings directly. Leaf `None`→settings fallbacks remain for legacy/non-routed callers only; tests must prove the routed path passes concrete knobs all the way down.
5. **`Strategy.execute(standalone_question) -> SelectionResult`**, not a bare context list — `answer_service` gates on `pre_expansion` and builds from `selected`, and the trace records all three stages.
6. **Trace: add `retrieval_strategy: {strategy, knobs}` from the resolver; drop the preset-owned knob lines from `_feature_flags()`** (resolved knobs get exactly one trace source). Keep the trimmed runtime block for non-preset facts (reranker backend, answerability, edge/subquery/generator toggles, `min_chunks_for_answer`).
7. **Circular-import guard:** if `context_selection` takes a `RetrievalKnobs` parameter while the default strategy calls `select_context`, keep the strategy's `select_context` import inside `execute()` (or `TYPE_CHECKING` for `SelectionResult`).

**Required tests:** default resolver equals current settings field-by-field; a pinned fake preset beats monkeypatched settings through the full `select_context()` path; edge-expansion rerank uses resolved `rerank_top_n`, not settings; packaged retrieval uses resolved knobs when `subquery_packaging_enabled=True`; trace contains `retrieval_strategy.strategy == "default"` with exact resolved knobs (including greeting/no-retrieval queries if traces are written for them).

**Done when:** every query runs through the `default` preset; eval preflight shows zero diff vs pre-refactor; resolved knobs visible in every trace.

### R3 — Trace-first preset candidates ($0)

1. **Citation check:** run the 4 `citation_lookup` rows plus a few synthetic article-level queries ("What does Art 308 say?") through retrieve-trace. Does weighting BM25 up actually rank statute-mention queries better? (BM25 tested badly on paraphrase; untested as a citation matcher.) No -> `citation_lookup` stays label-only; delete the stub.
2. **Amendment check:** trace the 13 `amendment_or_current_law` rows under `default`. Consolidation and operative preference already ship by default, so `default` may already serve them. Define `current_law` only from a concrete knob diff the traces justify.

**Done when:** each candidate has either a trace-justified knob diff or is demoted to label-only. Both demoting is an acceptable outcome — the router still ships as trace-labeling infrastructure that prices future lanes.

### R4 — Router in serving

**Build:** relocate the greeting-guard call site under the router; condensation -> classifier (R1's winning arm, temperature 0) -> `INTENT_TO_STRATEGY` -> strategy execution. Low confidence or parse failure -> `default`. Intent, confidence, and strategy name in the trace (distinct namespaces per rule 4).

**Done when:** every `raglab ask` / API query traces an intent and a resolved strategy; greetings still short-circuit; behavior is identical to R2 for any intent mapped to `default`.

### R5 — Predicted-strategy eval

Oracle-labels vs predicted-labels run pair on the 81 rows (the RAGAS row cache keeps the unchanged majority cheap). Judge on **changed-context rows only**, per the locked eval method. Graduation per preset: changed-row recall/precision up, no faithfulness regression, R1 router accuracy holding in-pipeline.

**Watch rows:** 22 (RPC via ICT), 42 (online vs ordinary libel), 46 (jurisdiction + prescription) are cross-source / two-part questions labeled `default` because no v1 lane exists for them. If they underperform, the confusion matrix will wrongly blame the classifier — the real gap is a missing lane.

## Roadmap after R5 — r/LawPH forum track

The real motivation: answering real forum questions (fact patterns, advice-shaped queries).

- **First step ($0, no corpus change):** sample ~50 top r/LawPH questions; hand-tag topic + whether the current corpus could answer. The gap analysis picks sources to add, shows which OOS fences are touched, and doubles as a classifier stress-test set.
- **OOS moat conflict:** the 12 out-of-scope eval rows deliberately fence tax, civ-pro, election, SSS, customs, securities, wiretap. Expanding the corpus into a fenced area means consciously retiring/replacing that fence row — decide per area; refusing well is also demo-able behavior.
- **Advice lane = the first real agency increment:** fact patterns are genuinely multi-issue, so a future `fact_pattern_or_advice` strategy becomes the first strategy that is a *plan* (issue-spot -> retrieve per issue -> merge contexts -> generate) rather than a knob bundle — reviving decomposition where the data finally justifies it, contained to one lane while doctrine queries keep the deterministic fast path.
- **Caveats:** advice answers have no ground truth (reference-free metrics or a hand-curated subset); "advice" framing eventually needs a generator-side change (information-about-the-law posture, disclaimer) — the one place the generation-untouched rule will bend for this goal.
