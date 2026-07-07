# Strategy Router — Plan

**Status:** locked 2026-07-07. R1 tooling (00cb232, benchmark run pending), R2 (d07fef8, zero-diff preflight), and R3 (traced + decided, see R3 outcome) are implemented; R4/R5 are not. Build constraints amended after external reviews (see per-milestone "Binding" notes).

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
| `citation_lookup` | label only — R3 demoted; no sparse-depth counterfactual evidence | `default` |
| `list_or_rule_synthesis` | label only (parent expansion + k30 are already defaults) | `default` |
| `amendment_or_current_law` | preset registered by R3; prefer-operative won on the chain-of-custody row with no regressions. **Evidence is MiniLM-specific** (serving backend): under bedrock rerank the operative chunk doesn't survive the cut, so the preset no-ops there — harmless, but invisible to a bedrock-judged eval (see R5 caveat) | `current_law` |
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

**R1 outcome (2026-07-07, run `intent_ab_20260707_210542`):** **Haiku wins the router seat** — routed accuracy 57/81 (70.4%) vs mistral 45/81 and NLI 28/81; no arm within the 2–3-row gate window. Strategy-level (the behavior-relevant collapse per rule 4, since only amendment → `current_law` changes retrieval): Haiku 91.4% with **amendment precision 1.00** — zero false `current_law` activations; its 7 amendment misses are all implicit-signal hard cases routed safely to `default` (incl. the RA 11648 explicit-citation trap → `citation_lookup`, as rule 1 predicted). Haiku routes eval_051 (the row with the `current_law` evidence) correctly; mistral does not. mistral over-fires amendment (13 false activations, incl. watch-row 42). NLI never fires the lane (amendment R=0.00). Agreement stats: all-three-agree on 13/81, 13/13 correct — disagreement predicts error, but the agree-region is too small for a cascade; verification/cascade ideas stay dead for v1, with data. Zero parse failures, zero low-confidence on both LLM arms. R4 note: Haiku classification adds a ~0.5–1s Anthropic call in front of every query on serving surfaces.

**R1 addendum (2026-07-07, runs `intent_ab_20260707_212231` / `intent_ab_20260707_222634`; audited and corrected same night):** two challenge arms ran on the same frozen prompt and benchmark. Routed accuracy: `qwen3:4b` 69.1%, `gemma3:4b` 65.4% (vs Haiku 70.4%). But the row-level amendment-lane audit reshuffles the strategy-level (behavior-relevant) picture: **qwen3 75/81 (92.6%, amendment 7/13 hit, zero false fires) > Haiku 74/81 (91.4%, 6/13, zero false) = gemma3 74/81 (91.4%, 9/13 — best amendment recall — but 3 false fires)**. Corrections to first-pass claims: (1) gemma3 **does** route eval_051 correctly (the misidentified "miss" was dataset row 26, the §21 witnesses question — which Haiku also misses); every arm delivers the preset's evidence row except mistral. (2) qwen3's 21s median is **thinking tokens after all**: Ollama 0.31.1 silently ignores `think: False`, qwen3 emits ~1,700–2,100 reasoning tokens per call (prefill is 636 tok in 0.2s, 100% GPU, load 0.2s — GPU-spill and reload hypotheses disproven by API timing fields), and `llm_client`'s `</think>` strip made the stored outputs look clean. `/no_think` halves it (15.5s); a newer Ollama honoring the param would put qwen3 near gemma3's 1.8s — worth re-timing after an upgrade, since qwen3 would then lead the strategy-level board with zero false fires. (3) gemma3's 3 false fires audited: watch-row 42 online-libel (pre-flagged label ambiguity — RA 10175 genuinely changed the penalty; mistral fired too) plus min-wage and estate-tax OOS rows; since `current_law` = default knobs + `prefer_operative` (reorder-only, no-ops without a supersession pair in the retrieved set), the behavioral harm of all three is plausibly nil — checkable with a $0 trace. gemma3's remaining real defects: OOS recall 0.25, citation precision 0.22, degenerate self-reported confidence (always `high`). **Both follow-ups ran the same night; the seat decision is settled again, with better evidence.** (a) $0 harm trace: on all 3 gemma3 false-fire questions the selected context is byte-identical under `current_law` vs `default` (MiniLM, serving reranker) — `prefer_operative` no-ops without a supersession pair in the retrieved set, so the observed false fires are behaviorally harmless. (b) qwen3 re-time: no newer Ollama exists (0.31.1 is current) and the model blob is current; the model reasons in prose even with an empty `<think>` block force-prefilled, so the only fast mode is JSON-constrained decoding (`format: "json"`, 0.7s median). A labeled v2 pass over all 81 rows (`intent_ab_qwen3json_v2`) showed **the chain-of-thought was load-bearing: routed accuracy 61.7% (from 69.1%), amendment precision 0.60 (from 1.00, six false fires), strategy-level 87.7%** — qwen3 offers its quality or its speed, never both, and is out either way. **Final: Haiku on serving surfaces (only arm combining zero false fires, working OOS detection, and ~1.2s); local path routerless. gemma3 is a defensible local-seat fallback if one is ever required** (strategy-level parity with Haiku, best amendment recall 0.69, false fires proven behaviorally harmless) — R5 oracle-vs-predicted can settle that if the need arises. Measured latencies (median, fresh): Haiku 1.2s, mistral 0.9s, gemma3 1.8s, qwen3 21.0s thinking / 0.7s json-constrained, NLI 0.1s warm.

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

**Binding build notes (review 2026-07-07):**

1. **Decision rule = default trace plus minimal counterfactual arms, not a single-trace read.** All arms are pure `RetrievalKnobs` variants driven through the R2 layer, minilm, $0.
2. **Citation registration criterion:** `_fuse` is RRF (`1/(60+rank)`) — depth admits candidates but never re-weights an already-included target, so "target strong in sparse but lost in fusion" is unfixable by any of the 7 knobs. `citation_precision` registers only if the counterfactual (default vs `sparse_top_k=20` vs `30`) shows targets **outside sparse top-10 but inside the deeper cut** that then reach `selected`.
3. **Amendment registration criterion:** `prefer_operative` reorders only when both stale and operative chunks survive the post-rerank cut — candidate-set presence proves nothing. Run the real counterfactual (default vs `prefer_operative_enabled=True`); `current_law` registers only if a stale chunk is actually demoted below the operative target after the full downstream path on ≥1 row, with no row regressing.
4. **Instrumented stage trace:** replicate the pipeline stage-by-stage (hybrid → rerank → edge → prefer-op → parent → dedup, same functions/order, explicit knobs) recording the target's rank per stage; `SelectionResult`'s three snapshots are too coarse. Final sanity assert: script's end state equals the real `select_context` output, compared on **ordered chunk_ids at `selected`** — not scores (rerank mutates result objects; floats drift).
5. **Backend pinning:** `reranker_backend` defaults to `bedrock` — the script forces `minilm` via a scoped monkeypatch/restore of `settings.reranker_backend` (not env mutation after import) for the main arms; Bedrock spot-checks of near-cut rows are explicit and opt-in.
6. **Baseline assert:** write `resolve_knobs("default")` to `meta.json` and hard-fail if it differs from the intended baseline (k30/10/8, parent-exp on, prefer-op off, operative-only on, dedup on) — the stale-`.env` guard — with an explicit escape-hatch flag.

**R3 result (2026-07-07):** `scripts/trace_r3_candidates.py` run `data/eval_results/runs/2026-07-07/r3_trace_20260707_201442/` demoted `citation_precision` (no sparse-depth selected-target improvement) and registered `current_law` as default + `prefer_operative_enabled=True` (evidence: `eval_051`, no regressions).

**Done when:** each candidate has either a trace-justified knob diff or is demoted to label-only. Both demoting is an acceptable outcome — the router still ships as trace-labeling infrastructure that prices future lanes.

**R3 outcome (2026-07-07, runs `r3_trace_20260707_201442`/`_202924`):** `citation_precision` **demoted** — no sparse-depth improvement on any of the 8 probes via the outside-top-10 mechanism (the one near-miss, RA 9165 §5 at sparse rank 24, was cut by rerank — the unfixable-within-7-knobs pattern). `current_law` **registered** (`default` + `prefer_operative_enabled=True`) on eval_051: stale RA 9165 §21 at rank 2 over the operative RA 10640 version at rank 6 under default, flipped by prefer-operative; no regressions on the other 12 rows; result reproduced exactly across reruns. **Bedrock spot-check on eval_051 refutes the evidence on that backend:** bedrock rerank cuts the operative chunk entirely (stale stays rank 2), so prefer-operative no-ops — no benefit, no harm. Kept registered because serving pins MiniLM (a990aa7), where the evidence stands. Backlog (bedrock rerank quality, independent of router): bedrock drops the operative amendment on the stale-cross-reference row that MiniLM keeps — same family as the libel-row watch from the bedrock A/B.

### R4 — Router in serving

**Build:** relocate the greeting-guard call site under the router; condensation -> classifier (R1's winning arm, temperature 0) -> `INTENT_TO_STRATEGY` -> strategy execution. Low confidence or parse failure -> `default`. Intent, confidence, and strategy name in the trace (distinct namespaces per rule 4).

**Done when:** every `raglab ask` / API query traces an intent and a resolved strategy; greetings still short-circuit; behavior is identical to R2 for any intent mapped to `default`.

### R5 — Predicted-strategy eval

Oracle-labels vs predicted-labels run pair on the 81 rows (the RAGAS row cache keeps the unchanged majority cheap). Judge on **changed-context rows only**, per the locked eval method. Graduation per preset: changed-row recall/precision up, no faithfulness regression, R1 router accuracy holding in-pipeline.

**Backend caveat (from the R3 bedrock spot-check):** the `current_law` lane must be judged under the **serving reranker (MiniLM)** — its trace evidence exists only there; under bedrock the preset never fires, so a bedrock-judged R5 would auto-fail it as inert rather than measure it. Run the R5 pair with the reranker matched to serving for that lane (or note explicitly that the lane is bedrock-invisible).

**Watch rows:** 22 (RPC via ICT), 42 (online vs ordinary libel), 46 (jurisdiction + prescription) are cross-source / two-part questions labeled `default` because no v1 lane exists for them. If they underperform, the confusion matrix will wrongly blame the classifier — the real gap is a missing lane.

## Roadmap after R5 — r/LawPH forum track

The real motivation: answering real forum questions (fact patterns, advice-shaped queries).

- **First step ($0, no corpus change):** sample ~50 top r/LawPH questions; hand-tag topic + whether the current corpus could answer. The gap analysis picks sources to add, shows which OOS fences are touched, and doubles as a classifier stress-test set.
- **OOS moat conflict:** the 12 out-of-scope eval rows deliberately fence tax, civ-pro, election, SSS, customs, securities, wiretap. Expanding the corpus into a fenced area means consciously retiring/replacing that fence row — decide per area; refusing well is also demo-able behavior.
- **Advice lane = the first real agency increment:** fact patterns are genuinely multi-issue, so a future `fact_pattern_or_advice` strategy becomes the first strategy that is a *plan* (issue-spot -> retrieve per issue -> merge contexts -> generate) rather than a knob bundle — reviving decomposition where the data finally justifies it, contained to one lane while doctrine queries keep the deterministic fast path.
- **Caveats:** advice answers have no ground truth (reference-free metrics or a hand-curated subset); "advice" framing eventually needs a generator-side change (information-about-the-law posture, disclaimer) — the one place the generation-untouched rule will bend for this goal.
