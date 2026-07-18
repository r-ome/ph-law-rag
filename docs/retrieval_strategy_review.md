# Retrieval Strategy Review and Experiment Roadmap

**Date:** 2026-07-14

**Status:** Analysis complete; Phase 0 measurement foundation implemented

**Scope:** Retrieval behavior, especially Ambiguous and Paraphrase eval rows

## Executive Finding

The main problem is no longer simply that dense retrieval cannot find the right
source. Qwen3-Embedding materially improved paraphrase candidate recall. The
remaining failures are concentrated later in the pipeline:

1. The correct legal chunk is often present in the candidate pool but loses to
   a semantically adjacent provision during MiniLM reranking.
2. Provision-level chunking separates list items and exceptions that must be
   read together, while parent expansion fires only when at least two children
   of the same parent survive reranking.
3. The final context uses a fixed top-N selection instead of adapting to the
   question and the evidence already covered.
4. Corrective retrieval appends locally ranked additions to the existing
   context. It never globally reranks the combined candidate set, so weak
   corrective passages can survive.
5. The eval summary hides some of this behavior. RAGAS category counts include
   only scored answers, so abstained Ambiguous rows disappear from the category
   metrics.

The result is a pipeline that often retrieves the correct document family but
packages the wrong article, subsection, sibling, exception, or amendment for
generation. This is primarily a candidate-selection and context-construction
problem, not evidence that Qwen3-Embedding failed as the retrieval default.

## Current Pipeline

The current local path is:

```text
question
  -> optional query planner
  -> Qwen3 dense retrieval (top 30)
  +  BM25 sparse retrieval (top 10)
  -> reciprocal-rank fusion
  -> MiniLM reranking (top 8)
  -> edge expansion
  -> parent expansion
  -> consolidated deduplication
  -> evidence gate / optional corrective retrieval
  -> generation
```

Relevant implementation points:

- [`app/retriever/hybrid_retriever.py`](../app/retriever/hybrid_retriever.py)
  performs dense/sparse retrieval and RRF fusion.
- [`app/retriever/context_selection.py`](../app/retriever/context_selection.py)
  owns reranking, edge expansion, parent expansion, and deduplication.
- [`app/retriever/parent_expansion.py`](../app/retriever/parent_expansion.py)
  requires at least two surviving children by default before expanding a
  parent.
- [`app/pipeline/corrective.py`](../app/pipeline/corrective.py) retrieves and
  reranks additions separately, then appends them to the baseline context.
- [`app/pipeline/runner.py`](../app/pipeline/runner.py) already emits retrieved,
  pre-expansion, and selected snapshots in live traces.

## Eval Evidence

The comparison point is the locked 131-row Qwen/MiniLM baseline:

`data/eval_results/runs/2026-07-11/gemma4-e4b_qwen06-baseline-131_20260711_104509`

The latest CRAG experiment is:

`data/eval_results/runs/2026-07-14/gemma4-e4b_20260714_104240`

| Metric | Locked baseline | CRAG experiment |
|---|---:|---:|
| Rows | 131 | 131 |
| Scored answers | 113 | 114 |
| Abstention accuracy | 93.9% | 94.7% |
| Faithfulness | 0.8998 | 0.8967 |
| Answer relevancy | 0.7701 | 0.7655 |
| Context precision | 0.6868 | 0.6751 |
| Context recall | 0.8330 | 0.8198 |
| Paraphrase precision | 0.6386 (n=24) | 0.6057 (n=25) |
| Paraphrase recall | 0.8229 (n=24) | 0.7900 (n=25) |
| Ambiguous precision | 0.5833 (n=4) | 0.6111 (n=3) |
| Ambiguous recall | 0.6875 (n=4) | 0.5833 (n=3) |

This is not a clean causal A/B because the profiles, commits, evidence gate, and
corrective behavior differ. It does show that CRAG did not improve the current
retrieval baseline and that its extra context slightly reduced aggregate
precision and recall.

The `n` values above are scored answers, not category population. The dataset
contains six Ambiguous questions, all in the regression split. In the latest
run, three abstained and were excluded from the Ambiguous RAGAS averages. There
are currently no Ambiguous dev or holdout rows. Ambiguous category changes are
therefore high variance and can look better simply because difficult rows were
not scored.

## Representative Failures

| Eval row | What happened | Failure layer |
|---|---|---|
| `eval_038` - perfection of sale | The historical target, Civil Code Article 1475, was around candidate rank 21. MiniLM discarded it; the Qwen reranker previously placed it first. | Reranker bottleneck |
| `eval_053` - verbal sale of land | The context selected Article 1403(2)(d), while the applicable sibling was Article 1403(2)(e). Both share a provision parent, which makes document/provision-level checks look better than leaf accuracy. | Sibling fragmentation |
| `eval_056` - verbal loan | Retrieval favored Article 1144 and unrelated ten-year passages and missed Article 1145. CRAG still judged the context sufficient. | Adjacent-rule confusion and evidence-gate miss |
| `eval_102` - catcalling | Initial retrieval found a definition but not enough operative law. Corrective retrieval added an unrelated first-time-minor drug passage. | Additive corrective retrieval |
| `eval_052` - seller warranties | Correct Articles 1484 and 1486 were ranks 2 and 3, but an irrelevant Family Code passage was rank 1. | Poor top-order precision |
| `eval_055` / `eval_057` | Supporting context was present, but the gate or generator abstained. | Not a first-stage retrieval miss |

These examples show why source-level recall is insufficient. A legal retriever
must distinguish the exact operative leaf and retain the neighboring text
needed to interpret it.

## Why Paraphrase Questions Still Fail

Qwen3-Embedding addressed the lay-language-to-legal-language vocabulary gap. In
the matched graduation experiment, Paraphrase context recall improved by 0.11.
However, a broad semantic candidate pool contains many provisions that discuss
the same legal concept. MiniLM is then asked to make a fine legal distinction it
was not trained to make.

Increasing dense top-K exposed this weakness rather than fixing it. Correct
targets appeared at dense ranks 11-29, but MiniLM still removed important
passages such as Articles 3 and 1475 before the final top eight. No tested K won
consistently. This explains why deeper retrieval can produce mostly adjacent,
plausible-looking context without improving final recall.

The old decomposition and subquery-packaging experiments made this worse.
Atomic paraphrases were split into artificial facets, each facet introduced
additional adjacent provisions, and the same narrow reranking/context budget
had to absorb all of them.

## Why Ambiguous Questions Look Unstable

Ambiguous questions combine three separate cases:

1. The question is underspecified but a bounded legal answer is possible.
2. The relevant context is present, but the evidence gate or generator
   abstains.
3. Retrieval follows one plausible interpretation while the reference expects
   another.

The current report mixes these cases and then removes abstained rows from RAGAS
category averages. With only six regression rows, a one-row change moves the
category sharply. The first action for this category should be reporting and
diagnostic coverage, not tuning against the current aggregate.

## Existing Implementations and Their Status

| Mechanism | Status | Evidence and verdict |
|---|---|---|
| Qwen3 legal query embedding | Default | Graduated. Improved Paraphrase recall without weakening the out-of-scope fence. Keep it. |
| Parent expansion | Default | Successful. On changed rows it improved recall by 0.136. It still misses single surviving children because `min_children=2`. |
| Query decomposition | Behind flag, off | Failed its matched run. Synthesis recall improved only 0.02 while faithfulness fell from 0.75 to 0.58; Paraphrase faithfulness fell 0.11. Do not rerun unchanged. |
| Subquery packaging | Behind flag, off | Failed. Paraphrase recall fell from 0.94 to 0.14 in the recorded experiment. Do not rerun unchanged. |
| Qwen3 reranker | Research arm | Retrieval quality is strong, but local MPS/unified-memory behavior and Ollama model residency make it operationally fragile. |
| Bedrock reranker | Eval/host default by ADR | Matched Qwen-class quality and improved Paraphrase precision, but the non-adjustable 2 RPM quota prevents interactive serving. Targeted offline use remains viable. |
| MiniLM reranker | Serving default | Operationally viable but is the main selection bottleneck in difficult Paraphrase rows. |
| Intent router | Implemented | Only the `current_law` route materially changes retrieval. It changed 2 of 81 contexts in the matched test, so it does not solve general paraphrase selection. |
| CRAG evidence gate | Experimental | The judged experiment is flat-to-negative. It can declare weak context sufficient, and its corrective path appends rather than globally repacks. |
| `max_distance` tuning | Available | Functionally inert after hybrid fusion and reranking in the matched 0.5 versus 0.6 experiment. Do not prioritize. |
| Stage tracing | Implemented | Useful operational snapshots exist, but durable eval artifacts do not yet preserve dense and sparse candidate identities, target survival, or stage-specific retrieval metrics. |

## Ollama and Model-Residency Constraint

The model conflict should be treated as an execution-topology problem, not as
evidence against the Qwen embedding or reranker quality:

- Qwen3-Embedding runs through Ollama.
- Gemma4 generation runs through Ollama and can evict other resident Ollama
  models to obtain memory.
- The Qwen3 reranker runs through Hugging Face Transformers/MPS, not Ollama, but
  it competes for the same Apple unified memory.

The practical local experiment topology is two-phase:

1. Run and persist retrieval/reranking outputs for the complete eval set.
2. Release retrieval model memory, load Gemma4, and generate from the frozen
   contexts.

This makes matched reranker comparisons possible without requiring all models
to coexist. It also separates retrieval changes from generator nondeterminism.

## Retrieval-Only Metrics at Every Stage

### Storage decision

Use files as the decision-grade eval record and structured logs for operations:

- Keep timing, errors, model loading, and stage counts in structured logs.
- Write `retrieval_trace.jsonl` beside each eval run, one candidate per stage.
- Write `retrieval_summary.json` beside each eval run for aggregate metrics.
- Do not put the first version in SQLite. Add DB ingestion later only if the UI
  needs cross-run querying or dashboards. Files keep runs immutable, portable,
  diffable, and tied to their existing `meta.json`.

### Candidate record

Each trace record should include at least:

```json
{
  "eval_id": "eval_053",
  "stage": "dense|sparse|fused|reranked|expanded|selected|corrective",
  "query_variant": "original|legal_rewrite|facet",
  "rank": 1,
  "chunk_id": "...",
  "source_id": "civil_code",
  "provision_id": "article_1403_2_e",
  "parent_id": "article_1403_2",
  "raw_score": 0.0,
  "selected": true,
  "expected_source_match": true,
  "expected_provision_match": true
}
```

Dense, sparse, fused, and rerank scores must be separate fields. RRF currently
mutates `RetrievalResult.score`, which destroys the original score provenance
and makes some historical trace output misleading.

### Stage metrics

Report these for every stage and category:

- Hit@K and Recall@K for expected chunks/provisions.
- Mean reciprocal rank of the first exact provision hit.
- Target survival from dense/sparse to fused, reranked, expanded, and selected.
- Expected-source recall separately from exact-provision recall.
- Parent and sibling coverage.
- Irrelevant candidates introduced by expansion or corrective retrieval.
- Candidate count, final context token count, and latency.

The key diagnostic is target survival. It identifies whether a row failed
because the candidate generators missed it, fusion buried it, reranking removed
it, expansion failed to recover its legal family, or final packaging dropped it.

## Legal Query Separation

This should be a narrow legal-translation branch, not a return to general query
decomposition.

Proposed contract:

```json
{
  "legal_query": "Civil Code enforceability of an oral contract for the sale of land under the Statute of Frauds",
  "citations": [],
  "confidence": 0.84
}
```

Retrieval should run once with the original question and once with exactly one
legal rewrite. The two pools are deduplicated/fused, but the final reranker
scores candidates against the original user question. This preserves literal
terms while adding a legal vocabulary bridge.

Use the existing `claude-haiku-4-5` integration first. It is already used for
the router and avoids loading another Ollama model beside Qwen3-Embedding and
Gemma4. The model must run at temperature zero with a strict structured-output
parser and fall back to original-only retrieval on timeout, invalid output, or
low confidence.

This experiment must be evaluated only after stage-level retrieval metrics
exist. Its graduation question is whether the rewrite improves exact-provision
survival on Paraphrase and Ambiguous rows without hurting Factual or
out-of-scope behavior.

## Sibling-Aware Expansion

Phase 3 is an approved, opt-in experiment. It is not the same as current parent
expansion and it does not change the serving default or any intent-router
mapping.

The experiment runs after parent expansion and before the `expanded` candidate
snapshot and final deduplication:

```text
rerank -> edge expansion -> prefer operative -> parent expansion
       -> sibling expansion -> expanded snapshot -> dedup -> selected snapshot
```

Eligibility is determined from each retrieval result's metadata. A seed must
have `parent_key` and `unit_label`, and must not already be an
`expanded_from_parent` result. SQLite is used only to load the seed's family:
rows whose `metadata_json.parent_key` matches, ordered by `chunks.chunk_index`.
No schema migration or reindex is required.

A sibling leaf is the atomic group `(parent_key, unit_label)`. This preserves
all size-split parts of a leaf. Seeds are processed in fixed rerank order; for
each distance from 1 through the configured radius, the preceding leaf is
considered before the following leaf. An admitted leaf is inserted in document
order around its seed. An existing survivor or a leaf already admitted through
another seed is neither re-admitted nor charged to the budget twice. A leaf is
admitted whole or skipped whole.

The initial experimental settings are radius 1, 3,000 added characters, and
750 estimated tokens, with character and token budgets global to the query.
Admission uses deterministic first-fit budgeting: an over-budget leaf is
skipped, but a later smaller leaf may still be admitted. The eligibility census
is deliberately structural and does not apply these budgets, so any future
binding census must separately confirm that denominator leaves fit the declared
limits.
Sibling results are structurally retained without another reranker call. They
inherit the seed score for display only and carry `expanded_from_sibling`,
`sibling_seed_chunk_id`, and `sibling_offset` provenance. When operative-only
retrieval is enabled, an explicit `operability_action=hide` excludes a row;
missing operability metadata remains fail-open.

Sibling-expanded results are exempt from consolidated same-provision merging
and dropping, matching the protection already applied to parent-expanded
results. Recovery is attributed at the pre-dedup `expanded` snapshot and
checked again at `selected` so downstream loss is visible.
The current row-level selected-retention summary asks whether any target leaf
remains; for a future multi-target sibling-recovery row, inspect the candidate
trace to verify retention of the specific recovered leaf. Sibling-expansion
latency is reported both on its own and inside the aggregate `expanded` timing,
so stage timings are diagnostic categories and must not be summed.

The four behavior knobs are `sibling_expansion_enabled`,
`sibling_expansion_radius`, `sibling_expansion_max_chars`, and
`sibling_expansion_max_tokens`. They participate in Settings, policy behavior
identity, `RetrievalKnobs`, traces, and sealed-bundle selection comparison. The
pinned `sibling_aware` strategy is available only through explicit eval and
Retrieval Lab override surfaces.

Retrieval-only capture selects the arm explicitly with
`raglab eval-retrieve --strategy sibling_aware`; omitting the option preserves
the frozen default arm.

Evaluation first performs a read-only radius-1 eligibility census over sealed
non-holdout traces joined to local SQLite ordering. The binding retrieval gates
are aggregate recovery of leaves missed after reranking, survival through final
selection, no loss in selected exact-leaf coverage or source/provision recall,
and bounded context/latency growth. The target is at least 80% recovery of
radius-1-eligible misses subject to the query budget. If the census finds fewer
than six eligible rows, that percentage is descriptive rather than binding and
the decision rests on no-regression gates plus row-level inspection.
`eval_053` remains a named MiniLM smoke check only: Article 1403(2)(e) should be
recovered when its Article 1403(2)(d) seed survives reranking.

The implementation-time read-only census of the sealed
`phase2-original-minilm` non-holdout trace found seven exact-leaf rows missed
after reranking, but only one radius-1-eligible row: `eval_053`. The Phase 3
percentage gate is therefore descriptive for this experiment; the binding
decision uses the no-regression gates and row-level inspection. This census did
not run retrieval, generation, a model, or the holdout.

### Checkpoint 7 retrieval-only result (2026-07-16)

The explicit `sibling_aware` retrieval command, tagged
`phase3-sibling-aware-minilm`, completed all 131 dev and regression rows and
sealed a non-holdout bundle with matched capture consistency. Both source
bundles validated before comparison. Dataset, target, corpus, and index hashes
matched the frozen `phase2-original-minilm` baseline, and every pre-rerank pool
hash was unchanged.

The sibling arm changed 64 selected contexts, all additively: zero baseline
chunks were removed and there were no source-, provision-, or leaf-target loss
rows. `eval_053` was the only target gain; Article 1403(2)(e) was present at both
the expanded and selected snapshots. The candidate census reports 1/1 eligible
recovery (100%), explicitly descriptive at N=1. Selected exact-leaf coverage
improved by one row, from 0.5769 to 0.6154, while aggregate selected source
recall, parent-provision coverage, and target survival were unchanged.

The mechanism fired on 64/131 rows and added 167 chunks (mean 1.27 per query;
2.61 when fired). Only one addition was target-bearing. Mean final context grew
9.7%, mean retrieval latency grew 1.9%, and sibling expansion itself averaged
15.4 ms. The largest row stayed inside both declared limits at 2,977 characters
and 746 tokens. These results pass the retrieval-only gates in the predeclared
descriptive regime, but the low target-bearing ratio makes the matched
generation A/B a required next gate. No serving default, router mapping, ADR,
generation run, external model call, or holdout access followed from this
result.

The `eval-retrieval-compare` publisher was subsequently generalized from its
Phase 2-only arm contract to an explicit expected arm pair plus an exact
declared selection-knob delta. It validates both raw shared hashes, applies
frozen comparator-only defaults for sibling knobs absent from the pre-feature
baseline, treats profile labels as informational, and recursively rejects every
remaining shared-value difference before recording a canonical comparable
hash. It also rejects output-tag reuse across dated run directories.

The two sealed bundles were then durably compared under
`phase3-sibling-aware-minilm-comparison`: both arms were `original_only`, the
sole semantic delta was `sibling_expansion_enabled=false→true`, and the three
sibling limit knobs matched after frozen backfill. The artifact confirms 131
rows in identical order, zero changed pre-rerank pools, 64 changed selected
contexts, and the label-only `eval→local` difference as informational. Its
report SHA-256 is
`2025c19873954668e6a470634935f8cf16a32d4218be67f0f6f61209450615c6`.

No holdout, paid Bedrock run, ADR, automatic router mapping, or serving-default
change is part of Phase 3. A matched generation A/B follows only after the
retrieval gates pass and must inspect evidence-gate effects from the additional
selected chunks.

### Checkpoint 8 generation A/B result and graduation (2026-07-16)

Both frozen bundles were replayed through the same pinned `gemma4:e4b`
generator (`phase3-gen-baseline-gemma4`, `phase3-gen-sibling-gemma4`, 131 rows
each) and RAGAS-scored with the standing Haiku judge. Replay determinism held:
zero answer changes across the 67 unchanged-context rows, so every observed
difference traces to the 64 sibling-changed contexts.

Aggregates (baseline → sibling): context recall 0.833 → 0.857, context
precision flat at 0.687, faithfulness 0.900 → 0.894 (noise-sized), answer
relevancy 0.771 → 0.767. Abstention: answered 113 → 115, false abstentions
7 → 5 — `eval_056` and `eval_057`, both documented false-abstention failure
rows, flipped abstain → answer (faithfulness 1.00 and 0.86). No new false
abstentions; the out-of-scope moat is unchanged (11/12 correct abstentions,
the single answer leak pre-exists in both arms). `eval_053` is fixed
end-to-end: context recall 0 → 1.0, faithfulness 0.8 → 1.0, and the answer
cites Article 1403(2)(e) directly.

Changed-row inspection (per the locked judge-noise method): six rows improved
faithfulness by ≥0.15 (`eval_122`, `eval_039`, `eval_024`, `eval_105`,
`eval_001`, `eval_053`); two declined beyond noise. `eval_045` (1.00 → 0.33)
reads as a judge-method artifact — the sibling-arm answer is shorter and
correct, grounded in Art. III §17 which is in context. `eval_129`
(0.83 → 0.42) is the one genuine mechanism cost: the generator drifted onto
sibling-added ADR §11 exception subsections (its context recall rose
0.5 → 0.8). `eval_129` is the standing watch row.

**Verdict: GRADUATED.** Despite 166/167 additions being non-target-bearing,
precision held exactly flat and the faithfulness delta is inside judge noise,
while recall, abstention behavior, and the named failure rows all improved.
`sibling_expansion_enabled` defaults to `true` (ADR-026). The `sibling_aware`
preset stays registered for matched comparisons; `current_law` keeps its
pinned knobs; no intent mapping was added. Rollback is the single flag —
additive-only was verified per row. Artifacts:
`data/eval_results/runs/2026-07-16/phase3-gen-{baseline,sibling}-gemma4`,
`data/eval_results/diffs/diff_phase3-gen-sibling-gemma4.md`.

## Adaptive Final Context

Do not use one absolute score threshold across MiniLM, Qwen3, and Bedrock. Their
scores are not calibrated to the same scale.

A practical adaptive selector should:

1. Start with the top 3-4 reranked chunks.
2. Add a candidate only if it contributes a new source, provision,
   sibling/parent group, or evidence facet.
3. Stop when evidence coverage stabilizes or a token budget is reached.
4. Allow a larger cap when the legal-rewrite branch fires or evidence coverage
   remains uncertain, and the largest cap for a detected multi-facet synthesis
   question.
5. Deduplicate before token budgeting so repeated consolidated text does not
   consume the allowance.

Initial bounds to test, not yet accepted defaults:

- Factual: 3-4 chunks.
- Paraphrase/Ambiguous: up to 6 chunks.
- Synthesis: up to 8 chunks.

These category labels are for offline analysis only. Runtime selection must use
observable signals such as query-lane output, rewrite confidence, evidence-facet
coverage, candidate novelty, and token usage; it must not depend on hidden eval
labels.

This can first be evaluated by replaying stored reranked candidates. No model
calls are needed once candidate traces exist.

### Phase 4 offline mechanism implemented (2026-07-16; not graduated)

The opt-in experiment is implemented as a schema-1.1 replay only. The sealed
`phase3-sibling-aware-minilm` bundle's `selected_results` is the experiment's
full post-expansion, post-dedup packaging pool: the current serving pipeline has
no truncation or budget stage after dedup. Replaying that field is therefore
equivalent to placing the proposed selector immediately after dedup. It can
shrink the available pool but never fetch or widen retrieval.

`raglab eval-context-replay SOURCE_TAG --tag OUTPUT_TAG --selector
fixed|adaptive` validates and derives a new sealed non-holdout bundle without
calling retrieval, SQLite, rerankers, rewrite services, or generators. The
selector is score-agnostic. Contract v2 uses a four-chunk floor, ordinary/
uncertain/multi-facet soft caps of 7/11/11, two-bundle stabilization patience,
and a soft 2,400-token target. Its uniform estimator is
`ceil(len(build_context(results)[0]) / 4)`; stored chunk estimates are ignored.

Sibling additions are seed-centered atomic bundles. A bundle includes the seed
and every surviving result whose `sibling_seed_chunk_id` points to it, fires at
its first surviving pool position, preserves member order, and may cross both
the chunk cap and token target. Dangling seed IDs remain valid grouping keys.
This keeps the `eval_053` Article 1403(2)(c)-(e) group whole. Defensive dedup is
limited to duplicate chunk IDs, explicitly represented merged chunks, and exact
normalized text; provision identity never collapses distinct sibling leaves.

Replay republishes the terminal selected stage, context/source/prompt identities,
deterministic evidence detail, target presence, trace, summary, record hashes,
and publication hashes while asserting that `pre_expansion`, pre-rerank pool
hashes, evidence verdicts, and corrective behavior do not change. Comparator
identity includes the complete adaptive contract, with legacy bundles normalized
to the same defaults; matched arms may differ only on
`adaptive_context_enabled`.

This is an experiment mechanism, not a serving change. Schema 1.2, a live
`packaging_pool` capture, Settings/policy wiring, serving defaults, and an ADR
remain deferred until matched retrieval replay and optional generation A/B pass
their gates. The 35% mean rendered-token reduction ceiling is a halt/watch
condition, not a prediction or selector rule. There is no minimum required
reduction, but an inert selector cannot graduate. The holdout remains sealed.

### Phase 4 checkpoint 3 retrieval replay result (2026-07-16)

The adaptive replay sealed all 131 non-holdout rows as
`phase4-adaptive-context-minilm`; its matched comparison against
`phase3-sibling-aware-minilm` published as
`phase4-adaptive-context-minilm-comparison`. Dataset, targets, corpus, index,
embeddings, MiniLM reranker, cutoffs, evidence policy, and pre-rerank pools all
matched. The comparator recorded zero changed pre-rerank pools, 95 changed
selected contexts, and exactly one declared semantic delta:
`adaptive_context_enabled=false→true`. The comparison report SHA-256 is
`8786471ce5e2b48415480cf9410dacb2c36009db4fec2d5fab3c92848469ffd2`.

Packaging stayed inside the predeclared reduction watch: mean rendered context
fell 31.84% (1,552.9→1,058.5 estimated tokens), p95 fell 2,649→2,151, and the
maximum fell 3,274→2,563. Mean selected candidates fell 7.60→4.92. The selector
used cap 4 on 91 rows, cap 6 on 12, and cap 8 on 28; 21 rows crossed a numeric
cap only through atomic sibling bundles, and seven crossed the soft token target.
No selector input hash, evidence verdict, hard-abstention flag, or source breadth
increased or changed unexpectedly. The structural synthesis signal was broad:
only five of the 28 cap-8 rows had the offline Synthesis label, so its activation
distribution remains a redesign input rather than evidence of semantic facet
detection.

The binding target-preservation gate failed. Source-target coverage and exact-
leaf coverage were unchanged, and `eval_053` retained the complete Article
1403(2)(c)-(e) seed bundle. However, eight expected provisions were removed:
`eval_037` Article 282, `eval_039` Section 11, `eval_044` Article 1157,
`eval_074` Section 4, `eval_106` Section 4, `eval_109` Section 37,
`eval_124` Section 145, and `eval_129` Section 11. Aggregate selected parent-
provision coverage fell 0.7385→0.6961 and target survival 0.8529→0.8071. The
category provision-coverage changes were Factual 0.7868→0.7684, Paraphrase
0.6267→0.5333, and Synthesis 0.6471→0.5686; Ambiguous remained 0.9167.
`eval_129` lost the same Section 11 exception family implicated in the Phase 3
generation drift, which could be beneficial for generation, but it is still a
declared retrieval-target loss and cannot be waived post hoc.

**Verdict: HALT before generation.** The mechanism is operationally sound and
passes its context-size, source, leaf, OOS-breadth, evidence, integrity, and named
`eval_053` checks, but it fails the binding no-provision-loss gate. Do not run a
generation replay, access holdout, add an ADR, wire a live selector, or change a
default. A follow-up plan must revise the cap/widening policy without tuning on
hidden eval labels, then publish a new write-once replay tag.

### Phase 4 checkpoint 4 contract-v2 replay result (2026-07-16)

The eight checkpoint-3 losses were attributed before changing the selector.
Seven were caused by the cap check firing before a later novel-provision bundle;
`eval_109` was caused by the 2,048-token stop. None was caused by defensive
dedup, atomic sibling grouping, or the two-bundle stabilization rule. The
smallest per-row cap requirements ranged from 5 to 11, while `eval_109` required
a token target of at least 2,394 to reach Section 37. On that evidence, contract
v2 keeps the four-chunk floor and patience of two, raises the ordinary/
uncertain/multi-facet caps to 7/11/11, and rounds the token target to 2,400. It
does not add a hidden-label or provision-target-aware signal.

The new adaptive replay sealed all 131 rows as
`phase4-adaptive-context-v2-minilm`; its matched comparison against the same
`phase3-sibling-aware-minilm` control published as
`phase4-adaptive-context-v2-minilm-comparison`. The comparator again recorded
zero changed pre-rerank pools and exactly one declared semantic delta,
`adaptive_context_enabled=false→true`; 66 selected contexts changed. The
comparison report SHA-256 is
`19bd5a4ef4e5ba7a21007a49ec02340c7fc3a3aacc5ff8076173c90249ac19bc`.

The binding retrieval gate now passes. All 117 expected provision targets that
were present in the fixed packaging pool remain present: selected parent-
provision coverage is restored to 0.7385, selected target survival to 0.8529,
and exact-leaf coverage remains 0.6154. No source target, evidence verdict, or
hard-abstention flag changed. `eval_053` still retains the complete Article
1403(2)(c)-(e) atomic bundle, and the revised arm is a strict row-wise superset
of the checkpoint-3 adaptive arm.

Context reduction remains useful and bounded. Mean rendered context falls
11.65% (1,552.9→1,372.1 estimated tokens), p95 falls 2,649→2,372, maximum falls
3,274→2,696, and mean selected candidates falls 7.60→6.66. Cap 7 applies to 91
rows and cap 11 to 40; stop reasons are cap on 41 rows, exhausted on 80,
stabilized on five, and token target on five. Five rows cross the soft token
target only at an admitted atomic boundary. The selector is non-inert and stays
well below the 35% reduction halt ceiling.

**Verdict: PASS the retrieval-only Phase 4 gate.** No generation, holdout
access, ADR, schema 1.2 work, live seam, or serving-default change was performed.
The contract-v2 bundle is eligible for a separately authorized matched
generation review; serving graduation remains pending that evidence and a
separate architecture/default decision.

This is a development-set gate, not out-of-sample evidence. The 7/11/11 caps
and 2,400-token target were selected after inspecting the eight losses on these
same 131 regression/dev rows; six recovered rows retain their last required
target with zero chunk slack, including cap-bound `eval_039` and `eval_129`,
while `eval_109` relies on the documented soft token overflow. Runtime selection
remains label-free, but the numeric policy is in-sample tuned. The sealed
30-row holdout remains untouched and is required as an explicit release gate
before any serving-default or ADR decision.

Comparator identity now treats subordinate adaptive knobs conditionally. When
one arm enables adaptive packaging, its active contract is copied onto the
disabled arm for comparison; when both arms are disabled, cap/token/contract
knobs are inert and omitted; when both are enabled, they remain strict. This
restores regeneration of both the sealed contract-v1 and contract-v2 comparisons
without changing either artifact.

### Phase 4 checkpoint 5 matched generation A/B (2026-07-17)

The contract-v2 retrieval bundle was replayed through the same pinned local
`gemma4:e4b` generator as the existing Phase 3 sibling-context baseline. The
candidate sealed as `phase4-gen-adaptive-v2-gemma4` with 131 regression/dev
rows; no holdout row was read. Reusing the already sealed
`phase3-gen-sibling-gemma4` baseline avoided redundant generation and is
empirically matched: all 65 rows with byte-identical selected context reproduced
byte-identical answers and abstention decisions. Of the 66 context-changed rows,
43 changed their generated answer.

RAGAS used the standing `claude-haiku-4-5-20251001` judge: 54 of 116 candidate
answer rows were cache hits and 62 were newly judged. Aggregate baseline→v2
scores are faithfulness 0.8939→0.8838, answer relevancy 0.7667→0.7523,
context precision 0.6867→0.6743, and context recall 0.8572→0.8549. The candidate
has one extra answered row, so the matched 115-row comparison is the cleaner
effect estimate: faithfulness -0.0083, relevancy -0.0143, precision -0.0109,
and recall +0.0029. On the 98 common answered rows with a present retrieval
target, faithfulness changes -0.0032 and recall +0.0068; both satisfy the
standing no-greater-than-0.01 target-slice regression gate. Target-slice
precision changes -0.0128 and relevancy -0.0084.

Abstention improves: correct decisions rise 125→126, false abstentions fall
5→4, and the one pre-existing out-of-scope answer leak is unchanged. The sole
flip is `eval_112`, which changes from abstention to a supported partial answer
that correctly states DSWD applies RA 9344 disciplinary measures when a minor
commits the Safe Spaces Act offense. There are no generation errors, invalid
scores, or new warning classes.

Manual review covered all 54 target-present rows whose context changed (35
changed answers and 19 byte-identical answers). Most answer changes are
paraphrase/length effects, and several large judge movements occur on
substantively equivalent or byte-identical answers. One clear new mechanism
cost remains: `eval_124` retains IP Code Section 145 in the selected context,
but the v2 generator overlooks it, says trademark duration is unavailable, and
shifts to special copyright-duration rules; its context-recall score falls
0.25. `eval_037` is a secondary directness watch: the candidate retains Article
282 but adds that the context does not establish whether stealing is an
enumerated termination ground. `eval_129` is byte-identical to the Phase 3
baseline because contract v2 retains its complete seven-chunk context, so the
known Section 11 generation drift is neither fixed nor worsened.

**Verdict: PASS the matched development-set generation gate; HOLD serving
graduation.** The predeclared leakage, abstention, target-slice faithfulness,
and target-slice recall gates pass, with `eval_124` and `eval_037` carried as
explicit watch rows. This does not cure the in-sample cap tuning described
above. No holdout access, ADR, schema/live seam, or serving-default change
follows. The sealed holdout remains a mandatory separately authorized release
gate.

Artifacts: `data/eval_results/runs/2026-07-17/phase4-gen-adaptive-v2-gemma4`
(generation bundle SHA-256
`7558be0779fcc793d641298f89953e9ab3e8836e58d87ca948c1b416a5934691`)
and `data/eval_results/diffs/diff_phase4-gen-adaptive-v2-gemma4.md` (SHA-256
`8e75b93ba2c0bd99f1ba751c5fdf5ce47565b7d6a41cfb7c9124bb3a35be09bb`).

## Corrective Retrieval With Global Reranking

The current corrective implementation reranks additions against the question
but only relative to other additions. The highest corrective candidate always
defines the margin. With Qwen3/Bedrock, the MiniLM-oriented margin is not
meaningful; with MiniLM, at least the top addition survives even when it is
weak. The surviving additions are then appended after the baseline context.

The replacement experiment should:

1. Retrieve bounded candidates for missing facets.
2. Union them with the original pre-selection candidate pool.
3. Deduplicate by chunk and consolidated provision identity.
4. Rerank the entire union once against the original question.
5. Run the same adaptive final-context selector over the globally ordered pool.

This tests corrective retrieval as a candidate-discovery mechanism rather than
as an unconditional context append.

## Category and Abstention Reporting

Each category report should include:

- total rows;
- answered rows;
- abstained rows;
- expected abstentions;
- correct abstentions;
- false abstentions;
- answer leaks on expected-abstain rows;
- retrieval target present despite abstention;
- RAGAS means over answered rows;
- retrieval-only means over all rows.

Ambiguous reporting should also separate underspecified-but-answerable rows from
rows whose expected behavior is abstention or clarification. Until the category
has dev and holdout coverage, it should be treated as a diagnostic slice rather
than a stable optimization target.

## Historical Experiments Worth Rerunning

| Experiment | Rerun? | Conditions |
|---|---|---|
| MiniLM versus Qwen3 reranker | Yes | Retrieval-only over the frozen 131-row non-holdout set, with generation in a separate process. |
| Legal translation versus original-only | Yes, new experiment | After stage metrics exist; one rewrite only, exact-provision success criterion. |
| Adaptive context selection | Yes, new experiment | Offline replay from frozen reranked candidate traces. |
| Corrective global rerank | Done (Phase 5, 2026-07-18) | Redesigned and run as Phase 5; CP3 passed, shelved at CP5 (yield 4/26 fired rows vs per-query checker cost; failure mode is pool recall, not ranking). |
| Bedrock reranker | Targeted only | Use difficult Paraphrase/Ambiguous probes or cached reranks because of the 2 RPM quota. |
| Sibling-aware expansion | Yes, new experiment | Plan separately; compare against current parent expansion. |
| Query decomposition | No | Do not rerun unchanged; prior mechanism was negative. |
| Subquery packaging | No | Do not rerun unchanged; prior mechanism was strongly negative. |
| Additive CRAG | No | Current scored run is flat-to-negative and exposes a design problem. |
| `max_distance` 0.5 versus 0.6 | No | Already shown inert downstream. |

Experiments before 2026-06-18 used the older 70-row dataset, corpus/index, and
generator behavior affected by the determinism bug. They remain useful as
mechanism evidence but are not directly comparable to the current 131-row Qwen
baseline. The deterministic parent-expansion experiment from 2026-06-18 is the
strongest reusable historical evidence.

The old [`scripts/trace_topk_sweep.py`](../scripts/trace_topk_sweep.py) should be
repaired before reuse:

- its raw Qwen query bypasses the production legal retrieval instruction;
- `_fuse()` mutates candidate scores to RRF values, so displayed dense scores
  are not reliable after fusion.

## Proposed Phase Order

This is sequencing guidance only. Each phase will receive a separate plan and
approval before implementation.

| Phase | Goal | Dependency |
|---|---|---|
| 0. Measurement foundation — implemented 2026-07-14 | Persist per-stage candidates and retrieval-only metrics; add complete category/abstention reporting. | None |
| 1. Reproducible retrieval harness | Separate retrieval/reranking from Gemma4 generation and repair the stale top-K harness. | Phase 0 |
| 2. Legal query separation | Test original plus one Haiku legal rewrite. | Phases 0-1 |
| 3. Sibling-aware expansion | Recover bounded legal siblings missed by leaf reranking. | Phase 0; plan next session |
| 4. Adaptive final context | Select evidence by coverage and token budget using frozen candidate replay. | Phases 0 and 3 |
| 5. Corrective global rerank | Union corrective and baseline candidates, globally rerank, then adaptively package. | Phase 4 |
| 6. Graduation and policy update | Run matched non-holdout A/Bs, inspect category regressions, and update ADR/project plan only for mechanisms that graduate. | Phases 1-5 |

The 30-row write-once holdout remains untouched until a complete candidate
configuration passes its predeclared non-holdout gates.

### Phase 0 implementation note

Phase 0 adds opt-in internal candidate snapshots at the real discard points,
preserves dense and sparse score provenance through clone-based RRF, and writes
sentinel-protected `retrieval_trace.jsonl` plus `retrieval_summary.json` files
beside each eval run. `data/eval_retrieval_targets.jsonl` covers regression and
dev only; ten ambiguous indexed-namespace mappings are explicitly curated and
the four `in general` citations are evaluated source-only. Serving defaults,
candidate ordering, cutoffs, selected context, the API, and the frontend are
unchanged. Holdout release runs omit candidate and target-quality records and
retain aggregate operational counts and retrieval latency only.

## Review Classification Against the Project Plan

| Finding | Classification |
|---|---|
| Qwen3 embedding, parent expansion, and explicit context-selection stages beyond the original basic hybrid design | Worthwhile improvement, already evidenced |
| RRF overwriting the only score field | Technical debt; blocks trustworthy stage analysis |
| MiniLM dropping known relevant deep-pool candidates | Quality bug/serving constraint, not an implementation correctness bug |
| Corrective retrieval appending separately ranked additions | Retrieval-design bug |
| Ambiguous category metrics excluding abstentions without an all-row companion report | Evaluation-methodology bug |
| General decomposition and subquery packaging retained behind flags after negative experiments | Acceptable research code while off by default |
| Legal rewrite, sibling expansion, adaptive selection, and global corrective rerank | Final dispositions: sibling expansion (ADR-026) and adaptive selection (ADR-027) graduated to accepted architecture; legal rewrite (Phase 2, clean negative) and global corrective rerank (Phase 5, shelved at CP5 on CP3 evidence) remain registered research arms, off by default |

## Decision Guardrails for the Next Plans

- Preserve Qwen3-Embedding as the current local baseline.
- Keep MiniLM as the serving baseline until a replacement clears both retrieval
  quality and operational constraints.
- Do not use generation metrics alone to diagnose retrieval.
- Do not compare experiments with different candidate pools, rerankers, or
  generators as if they were single-variable A/Bs.
- Keep the holdout sealed.
- Prefer offline replay for selector and packaging experiments.
- Record target-slice improvement, non-target regression, abstention behavior,
  latency, and changed-context rows for every graduation decision.
# Phase 1 retrieval harness note (2026-07-14)

Phase 1 freezes retrieval preparation independently of answer generation. The
production stage order is shared with retrieval-only capture, while replay uses
`app.pipeline.frozen_generation` and validates selected-context, source-map,
context-block, and rendered-prompt hashes before making a generator call.

Candidate snapshots remain canonical in frozen records; the compatible trace and
summary are derived at seal. Rows are canonical JSON with per-row hashes and
ordered aggregate identity. Retrieval capture does not append session turns or
operational traces. The CLI rejects holdout before dataset loading or bundle
creation, and generator overrides are confined to replay generation.

The implementation leaves serving retrieval ordering, cutoffs, selection policy,
evidence policy, routing defaults, API contracts, and database schema unchanged.
Every sealed bundle records dataset/target identities, resolved configuration,
relevant source hashes, SQLite corpus hashes, BM25 hashes, combined index
identity, per-row record hashes, and ordered pool/context hashes. Reranker arms
are comparable only when dataset, target, corpus/index, and score-free ordered
pre-rerank-pool identities match; generation arms must replay one sealed bundle.
Retrieval-model unload remains best-effort and records warnings, with process exit
as the definitive memory boundary.

Capture also recomputes the SQLite, BM25, Qdrant, and combined index identities
after the final retrieval row. A mismatch or end-fingerprint failure writes a
failed state and refuses sealing, preventing one bundle from spanning an index
change. Replay tests independently corrupt every context/source/prompt identity,
and finalization counters cover normal, abstention, error, retrieval-only,
generation replay, and sealed replay-resume paths.

# Phase 2 Implementation Handoff: Legal Query Separation

## Status

The plan is executor-ready after two review passes. No Phase 2 implementation or
live/paid experiment has been performed.

Before changing anything:

- Read `AGENTS.md`, `docs/project_plan.md`, `docs/retrieval_strategy_review.md`,
  Phase 0/1 code and tests, and the complete dirty diff.
- Preserve every existing uncommitted change.
- Keep the 30-row holdout sealed; never create holdout retrieval targets.
- Work through the four checkpoints in order and stop for approval after each.
- Use the model-router at the start. Delegation requires explicit authorization.
- First persist this approved specification in
  `docs/retrieval_strategy_review.md`.

Fixed baselines:

- MiniLM serving reranker
- Qwen3-Embedding, 1024 dimensions
- existing retrieval ordering, cutoffs, context selection, evidence policy, and
  public `answer()` contract
- no decomposition, subquery packaging, or general multi-query expansion
- no database migration or ADR
- no changes to `app/eval_store.py`, eval API routes, generated frontend schemas,
  or React components

Known unrelated failure:

`tests/integration/test_sync_incremental.py:192`

# Checkpoint 1: Phase 1 live entry verification

Do not run paid Bedrock work without explicit authorization.

## Configuration preflight

```bash
uv run raglab show-config
```

Require:

- `embedding_backend=ollama`
- Qwen3-Embedding, dimension 1024
- `RAGLAB_PROFILE=eval` for captures
- no concurrent sync/indexing

Do not explicitly override standard embedding model/dimension defaults.

## eval_001 rank-1 gate

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression \
  --row-id eval_001 \
  --tag phase1-gate-eval001-minilm

uv run python - <<'PY'
from app.evals.integrity import paths_for, validate_sealed_bundle

rows, _ = validate_sealed_bundle(paths_for("phase1-gate-eval001-minilm"))
row = rows[0]
target = "constitution_1987:article-iii:section:13"

reranked = next(
    stage for stage in row["candidate_stages"]
    if stage["stage"] == "reranked"
)
selected = next(
    stage for stage in row["candidate_stages"]
    if stage["stage"] == "selected"
)

assert reranked["candidates"][0]["metadata"]["provision_id"] == target
assert any(
    candidate["metadata"].get("provision_id") == target
    for candidate in selected["candidates"]
)
print("eval_001 rank-1 and selected-context gate passed")
PY
```

## Memory-release gate

```bash
uv run python - <<'PY'
import json
from app.evals.integrity import paths_for

meta = json.loads(
    paths_for("phase1-gate-eval001-minilm").meta.read_text()
)
release = meta["memory_release"]

assert release["attempted"] is True
for component in ("reranker", "embedding"):
    report = release[component]
    assert report["attempted"] is True
    assert "result" in report
    assert "warning" in report

print(json.dumps(release, indent=2))
PY
```

Warnings remain bundle evidence but must be understood before Phase 2. Process
exit is the hard memory boundary.

## Matched reranker bundles

Run sequentially in separate processes:

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --tag phase1-gate-minilm

RAGLAB_PROFILE=eval RERANKER_BACKEND=qwen3 \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --tag phase1-gate-qwen3

RAGLAB_PROFILE=eval RERANKER_BACKEND=bedrock \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --tag phase1-gate-bedrock
```

The Bedrock command is paid.

```bash
uv run python - <<'PY'
from app.evals.integrity import paths_for, validate_sealed_bundle

tags = [
    "phase1-gate-minilm",
    "phase1-gate-qwen3",
    "phase1-gate-bedrock",
]
metas = {
    tag: validate_sealed_bundle(paths_for(tag))[1]
    for tag in tags
}
baseline = metas[tags[0]]

for tag in tags[1:]:
    candidate = metas[tag]
    assert candidate["dataset_identity"] == baseline["dataset_identity"]
    assert candidate["targets_identity"] == baseline["targets_identity"]
    assert candidate["corpus_identity"]["hash"] == baseline["corpus_identity"]["hash"]
    assert candidate["index_identity"]["hash"] == baseline["index_identity"]["hash"]
    assert (
        candidate["ordered_pre_rerank_pool_hash"]
        == baseline["ordered_pre_rerank_pool_hash"]
    )

print("Phase 1 reranker bundles are comparable")
PY
```

Stop after recording the gate results.

## Checkpoint 1 execution results (2026-07-15)

Checkpoint 1 completed through the two non-paid reranker arms. The paid Bedrock
arm remains unrun pending separate authorization.

- `uv run raglab show-config` passed with `embedding_backend=ollama`, the
  derived default `embedding_model=qwen3-embedding:0.6b`, and derived
  `embedding_dim=1024`. Neither `EMBEDDING_MODEL` nor `EMBEDDING_DIM` was set in
  the process environment. `RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm`
  resolved the capture profile to `eval` with the same embedding identity.
- Read-only process/file checks found no active Python sync, reindex, indexing,
  or `eval-retrieve` owner on the SQLite or BM25 paths. Qdrant was reachable
  through the existing port-6333 listener and Ollama through its
  `127.0.0.1:11434` listener once the exact commands were run outside the
  delegated worker's localhost-restricted sandbox.
- `phase1-gate-eval001-minilm` sealed successfully. The exact assertion passed:
  `constitution_1987:article-iii:section:13` was reranked at rank 1 and remained
  in selected context.
- The `eval_001` memory-release gate passed. Both `reranker` and `embedding`
  reported `attempted=true`, `result=true`, and `warning=null`; the bundle-level
  warning was also null.
- `phase1-gate-minilm` and `phase1-gate-qwen3` each sealed 131 rows in separate
  processes and independently passed `validate_sealed_bundle()`. Both bundles'
  reranker and embedding release hooks reported `result=true` with no warning.
- The non-paid comparability assertion passed: MiniLM and Qwen3 have equal
  dataset identity, targets identity, corpus hash, index hash, and
  `ordered_pre_rerank_pool_hash`.
- No generation, Haiku, holdout, sync, or indexing run occurred. Existing dirty
  application, API, frontend, eval-store, database, project-plan, and test files
  were not changed by Checkpoint 1.
- `phase1-gate-bedrock` was not run. On 2026-07-15 the paid Bedrock arm was
  explicitly deferred. No three-way MiniLM/Qwen3/Bedrock comparability claim is
  made; the validated MiniLM/Qwen3 comparison is the recorded Checkpoint 1
  evidence.

Checkpoint 1 is closed with Bedrock deferred. Checkpoint 2 remains paused for
its separate approval.

# Checkpoint 2: Schema 1.1 and bundle comparator

This checkpoint must not introduce Haiku or two-lane behavior.

## Schema rule

Bump frozen-context schema from 1.0 to 1.1.

Validation must accept minor versions 0 and 1 and reject later unsupported
minors.

All new `eval-retrieve` runs, including original-only runs, emit schema 1.1.

## Canonical pool semantics

Every 1.1 record must contain exactly one:

```json
{
  "stage": "fused",
  "query_variant": "combined",
  "pool_role": "pre_rerank_pool"
}
```

Original-only 1.1 snapshots:

```text
dense/original
sparse/original
fused/original lane
fused/combined pre_rerank_pool
reranked
expanded
selected
```

The combined pool is an ordered clone of the original lane’s fused candidates.
It must not modify candidate result metadata.

Hash dispatch:

```python
def _pre_rerank_pool_hash(candidate_stages, *, schema_minor):
    if schema_minor == 0:
        # Require the single legacy fused snapshot.
        ...
    elif schema_minor == 1:
        # Require exactly one pool_role="pre_rerank_pool".
        ...
    else:
        raise ValueError("unsupported frozen-context schema minor")
```

The hash payload remains only:

```json
{
  "chunk_id": "...",
  "text_hash": "..."
}
```

Therefore Phase 1 1.0 and original-only 1.1 must have equal:

- pre-rerank pool hash
- candidate order entering reranking
- selected-context hash
- selected result content and metadata

Their trace and summary file hashes are expected to differ because 1.1 adds
diagnostic lines.

## Trace and metric aggregation

Lane-level fused snapshots remain in `retrieval_trace.jsonl`.

Only `pool_role="pre_rerank_pool"` contributes to:

- aggregate fused metrics
- aggregate fused stage counts
- `pre_rerank_pool_hash`

Lane fused metrics are reported separately by `query_variant`.

`stage_candidate_counts["fused"]` counts only the canonical combined pool. A
separate lane breakdown may report:

```text
fused.original
fused.legal_rewrite
```

Do not count both the lane-fused snapshot and its canonical clone in
`candidate_count`.

Dense and sparse results remain reported by query variant.

## Separable retrieval identity

Schema 1.1 retrieval identity:

```json
{
  "retrieval_config": {
    "shared_values": {},
    "shared_hash": "...",
    "query_separation": {
      "arm": "original_only",
      "contract_version": 1,
      "prompt_version": "v1",
      "prompt_hash": "...",
      "model": "claude-haiku-4-5",
      "timeout_seconds": 15.0,
      "max_tokens": 160
    },
    "full_hash": "..."
  }
}
```

Rules:

- `shared_hash` covers common retrieval policy, embeddings, reranker, cutoffs,
  context selection, and evidence behavior.
- `arm` is excluded from `shared_hash`.
- `full_hash` covers the shared hash and complete query-separation object.
- Resume compares the full object/full hash.
- Phase 1 1.0 adapts its existing retrieval hash as the shared hash and implicitly
  uses `original_only`.

## Comparator

Create:

`app/evals/retrieval_comparison.py`

CLI:

```text
raglab eval-retrieval-compare BASELINE_TAG CANDIDATE_TAG --tag REPORT_TAG
```

It must:

- validate both sealed bundles;
- reject holdout metadata;
- compare `shared_hash`;
- compare query-separation configuration with `arm` removed;
- require baseline arm `original_only`;
- require candidate arm `original_plus_rewrite`;
- compare dataset, targets, corpus, index, embeddings, reranker, cutoffs,
  selection, and evidence identities;
- report pool/context changes by row;
- never import retrieval, generation, embeddings, rerankers, or Haiku.

Checkpoint tests must prove original-only 1.1 pool/selection parity with Phase 1
1.0 without asserting trace-file parity.

Stop for review.

# Checkpoint 3: Strict legal rewriter and paid-call cache

This checkpoint remains isolated from the production preparation path. Use
mocked tests only.

## Configuration

Add infrastructure settings only:

```python
legal_query_rewrite_model: str = "claude-haiku-4-5"
legal_query_rewrite_timeout_seconds: float = 15.0
legal_query_rewrite_cache_dir: str = "data/eval_results/legal_rewrite_cache"
```

Do not add an enable flag to `Settings`, `AnswerPolicy`, `BEHAVIOR_FIELDS`, or
`RetrievalKnobs`.

Constants:

```python
LEGAL_REWRITE_CONTRACT_VERSION = 1
LEGAL_REWRITE_PROMPT_VERSION = "v1"
LEGAL_REWRITE_MAX_TOKENS = 160
```

## Module and contract

Create:

`app/retriever/legal_query_rewriter.py`

Exact model output:

```json
{
  "legal_query": "<original query verbatim> | Legal terms: <one legal-language rendering>",
  "citations": [],
  "confidence": "high"
}
```

Rules:

- exact keys only;
- raw JSON only—reject code fences and surrounding prose;
- citations must be exactly `[]`;
- confidence must be `high` or `low`;
- only `high` may activate the rewrite;
- legal query must begin with the exact original query;
- fixed delimiter: `| Legal terms:`;
- non-empty suffix;
- no multiline output or alternatives;
- maximum `len(source_query) + 300` characters;
- reject new RA/BP/EO/Article/Section/G.R. numeric identifiers unless already
  present in the source query;
- reject answer prose;
- any rejection falls back to original-only retrieval.

Decision:

```python
@dataclass(frozen=True)
class LegalRewriteDecision:
    source_query: str
    legal_query: str | None
    confidence: Literal["high", "low"] | None
    status: Literal["disabled", "accepted", "fallback"]
    parser_outcome: Literal[
        "not_called", "valid", "invalid", "literal_violation"
    ]
    fallback_reason: Literal[
        "disabled",
        "low_confidence",
        "invalid_output",
        "literal_violation",
        "timeout",
        "llm_error",
        "interrupted_after_request",
    ] | None
    model: str | None
    prompt_version: str
    prompt_hash: str
    raw_output_hash: str | None
    call_latency_ms: float | None
    cache_key: str | None
    cache_status: Literal[
        "bypassed", "miss_written", "hit", "pending_recovered"
    ]
```

Never persist raw output, credentials, or request headers.

## Haiku call

Use the existing Anthropic dependency and credentials through a lazy direct call:

```python
client = anthropic.Anthropic(
    api_key=settings.anthropic_api_key.get_secret_value(),
    timeout=settings.legal_query_rewrite_timeout_seconds,
)

client.messages.create(
    model=settings.legal_query_rewrite_model,
    max_tokens=LEGAL_REWRITE_MAX_TOKENS,
    temperature=0,
    system=LEGAL_REWRITE_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": rendered_prompt}],
)
```

Do not modify the general generation seam.

## Cache

Key:

```python
sha256(canonical_json({
    "contract_version": 1,
    "prompt_version": "v1",
    "prompt_hash": "...",
    "model": "...",
    "max_tokens": 160,
    "source_query": "...",
}))
```

Layout:

```text
data/eval_results/legal_rewrite_cache/v1/
  .capture.lock
  <key>.pending.json
  <key>.json
```

Ordering:

1. Return a validated final record as a hit.
2. Exclusively create and fsync the pending marker.
3. Call Haiku once.
4. Parse and validate.
5. Atomically write/fsync the final record.
6. Remove pending only after the final record is durable.
7. Cache accepted and fallback results.
8. A pending record without a final record means `interrupted_after_request`; do
   not call Haiku again.

Use a non-blocking exclusive `flock` for the whole rewrite-enabled capture.
Acquire it before artifact creation or requests and release it in `finally`.

Required code justification:

> The process-wide capture lock complements per-key O_EXCL pending markers and
> prevents a concurrent process from mistaking an active paid request for crash
> residue.

This is intentionally a new Darwin/Linux coordination mechanism; per-record
durability continues using `O_EXCL`, `os.replace`, and `fsync`.

Required tests:

- strict parser cases;
- literal preservation;
- citation injection rejection;
- timeout/API/low-confidence fallbacks;
- call parameters;
- cache hit makes no call;
- all fallback decisions cached;
- pending recovery makes no call;
- lock contention rejects before artifact creation;
- lock releases after normal exit and crash simulation;
- cache key changes with prompt version/hash/model/query;
- raw output is represented only by hash.

Stop for review.

# Checkpoint 3 implementation results (2026-07-15)

Implemented the isolated legal-query rewriter infrastructure only: three
configuration defaults, strict raw-JSON/literal-preservation parser, lazy
Anthropic seam, durable versioned paid-call cache, and nonblocking process-wide
`flock` capture coordination. No serving or retrieval-preparation path imports
the module, no enable flag was added, and no paid or network call was made.

Mocked verification passed: `uv run pytest tests/unit/test_legal_query_rewriter.py -q`
(`22 passed`). Coverage includes parser and literal/citation rejection, call
parameters, every cached fallback, pending and malformed-final recovery without
a second call, whole-capture lock contention/release, cache identity and record
validation, and raw-output hashing without raw-output persistence.

Review follow-up registered `legal_query_rewrite_model`,
`legal_query_rewrite_timeout_seconds`, and `legal_query_rewrite_cache_dir` in
`INFRA_FIELDS` only. They remain excluded from `BEHAVIOR_FIELDS`, policy
resolution, and serving behavior. Full mocked unit verification then passed:
`uv run pytest -q tests/unit` (`291 passed`, with eight existing RAGAS import
deprecation warnings).

# Checkpoint 4: CLI-only two-lane retrieval

## Runtime arm seam

Define:

```python
LegalQuerySeparationArm = Literal[
    "original_only",
    "original_plus_rewrite",
]
```

Extend:

```python
def retrieve_rows(
    rows,
    *,
    tag: str,
    resume: bool = False,
    keep_retrieval_models: bool = False,
    query_separation_arm: LegalQuerySeparationArm = "original_only",
) -> Path:

def prepare_answer_state(
    state: AnswerState,
    *,
    strategy_override: str | None = None,
    query_separation_arm: LegalQuerySeparationArm = "original_only",
) -> AnswerState:
```

`retrieval_runner.py` must pass it explicitly:

```python
prepare_answer_state(
    state,
    query_separation_arm=query_separation_arm,
)
```

`run_answer()` continues omitting the argument. Serving is therefore
structurally fixed to `original_only`.

CLI:

```python
legal_query_separation: bool = typer.Option(
    False,
    "--legal-query-separation/--original-only",
)

arm = (
    "original_plus_rewrite"
    if legal_query_separation
    else "original_only"
)
```

Resume must reject an arm/full-identity mismatch.

## State and preparation order

Add:

```python
query_separation_arm: LegalQuerySeparationArm = "original_only"
legal_rewrite_decision: LegalRewriteDecision | None = None
```

Preparation:

```text
raw question
→ existing session-history rewrite
→ intent classification
→ retrieval planning
→ legal rewrite preparation
→ retrieval
→ unchanged evidence/corrective stages
```

The rewrite stage runs for all strategies, independent of intent.

For the experiment, fail before retrieval if either is enabled:

- `query_decomposition_enabled`
- `subquery_packaging_enabled`

## All-strategy interface

Extend:

```python
class Strategy(Protocol):
    def execute(
        self,
        question: str,
        knobs: RetrievalKnobs | None = None,
        *,
        legal_query: str | None = None,
    ) -> SelectionResult:
        ...
```

Apply the same signature to `StrategyPreset.execute()` and:

```python
def select_context(
    question: str,
    knobs: RetrievalKnobs | None = None,
    *,
    legal_query: str | None = None,
) -> SelectionResult:
```

`retrieve_context()`:

```python
def retrieve_context(state: AnswerState) -> None:
    decision = state.legal_rewrite_decision
    legal_query = (
        decision.legal_query
        if decision is not None and decision.status == "accepted"
        else None
    )

    state.selection = STRATEGIES[state.strategy_name].execute(
        state.effective_question or state.question,
        knobs=state.strategy_knobs,
        legal_query=legal_query,
    )
```

## Two-lane retrieval

Refactor around:

```python
def retrieve_hybrid_lane(
    query_text: str,
    *,
    query_variant: Literal["original", "legal_rewrite"],
    query_ordinal: int,
    knobs: RetrievalKnobs,
) -> list[RetrievalResult]:
    ...

def fuse_query_lanes(
    original: list[RetrievalResult],
    legal_rewrite: list[RetrievalResult] | None,
) -> list[RetrievalResult]:
    ...
```

Algorithm:

1. Original query runs dense and sparse retrieval.
2. Existing within-lane RRF produces the original fused pool.
3. An accepted rewrite independently runs dense and sparse retrieval with
   identical knobs.
4. Existing within-lane RRF produces its fused pool.
5. If rewrite is absent/fallback, return original fused result objects unchanged.
6. Otherwise equal-weight RRF the two lane pools using `RRF_K=60`.
7. Original lane precedes rewrite lane for deterministic ties.
8. Deduplicate by `chunk_id`.
9. Rerank the union exactly once against the original production retrieval query.
10. Keep existing rerank top-N, expansion, deduplication, context selection, and
    evidence policy unchanged.
11. Do not reserve rewrite slots or enlarge any cutoff.

## Score provenance

Keep `_retrieval_scores`; do not replace it.

Lane snapshots retain:

```text
dense_score
sparse_score
fused_score
```

The combined result may additionally contain:

```json
{
  "original_fused_score": 0.0,
  "original_lane_rank": 1,
  "legal_rewrite_fused_score": 0.0,
  "legal_rewrite_lane_rank": 3,
  "cross_query_rrf_score": 0.0
}
```

Fallback/original-only results must not receive new result metadata.

## Frozen rewrite record

Add:

```json
{
  "legal_query_separation": {
    "arm": "original_plus_rewrite",
    "source_query": "...",
    "source_query_hash": "...",
    "decision": {
      "status": "accepted",
      "legal_query": "...",
      "legal_query_hash": "...",
      "confidence": "high",
      "parser_outcome": "valid",
      "fallback_reason": null,
      "model": "claude-haiku-4-5",
      "prompt_version": "v1",
      "prompt_hash": "...",
      "raw_output_hash": "...",
      "call_latency_ms": 812.4,
      "cache_key": "...",
      "cache_status": "miss_written"
    },
    "semantic_input_hash": "..."
  }
}
```

`semantic_input_hash` excludes latency and cache status. Seal the ordered
bundle-level hash as
`ordered_legal_query_separation_semantic_input_hash`.

Generation replay must never call or import the rewriter.

## Required integration tests

- CLI arm reaches `retrieval_runner.py` and `prepare_answer_state()`.
- Both arms resolve to the same policy.
- `run_answer()` cannot enable rewriting.
- Original-only never imports or calls Anthropic.
- Default and `current_law` strategies both receive accepted rewrites.
- `eval_034` and `eval_053` exercise the lane under the default strategy.
- Both query paths run dense and sparse retrieval.
- Deterministic cross-lane RRF and deduplication.
- One rerank against the original query.
- Original-only/fallback pool and selected-context parity.
- No metadata mutation on fallback.
- Correct 1.1 snapshot order and canonical pool.
- Correct lane versus aggregate metrics.
- Resume after cached response makes no call.
- Resume after pending marker falls back without a call.
- Generation import isolation.
- Holdout rejection before cache, artifact, or model access.
- Comparator accepts the intended arm pair and rejects shared-config drift.

# Checkpoint 4 implementation results (2026-07-15)

Implemented the CLI-only `original_only` / `original_plus_rewrite` runtime seam.
Only `raglab eval-retrieve` exposes
`--legal-query-separation/--original-only`; `run_answer()` continues to omit the
seam and serving remains structurally fixed to `original_only`. Rewrite-enabled
capture acquires Checkpoint 3's nonblocking capture lock before locating or
creating eval artifacts and holds it through sealing, with context-manager
release on every exit. Holdout rows are rejected before the rewriter import,
cache, lock, artifact, or model seam. Resume validates the arm-bearing full
retrieval identity before processing rows.

The legal rewrite runs after history rewriting, intent classification, and
retrieval planning. The rewrite arm fails before rewriter or retrieval access if
query decomposition or subquery packaging is active. Accepted rewrites flow
through both the default and `current_law` strategies. Original and accepted
rewrite lanes independently run dense and sparse retrieval with the same frozen
knobs, use the existing within-lane RRF, and then enter deterministic equal-weight
cross-lane RRF with original-lane precedence for ties and `chunk_id`
deduplication. The combined pool is reranked once against the original production
retrieval query; all cutoffs, expansion, selection, evidence, and fallback
behavior remain unchanged. Original-only and fallback result metadata receive no
cross-query provenance.

Schema 1.1 capture now emits the ordered original lane, optional rewrite lane,
and exactly one canonical `fused/combined` `pre_rerank_pool`. Actual combined
results retain `_retrieval_scores` and add lane rank/score plus cross-query RRF
provenance. Frozen rows record the legal-query-separation decision and a semantic
input hash that excludes only call latency and cache status; sealing records
`ordered_legal_query_separation_semantic_input_hash`. Generation replay and
original-only capture do not import the legal rewriter or Anthropic.

Checkpoint 4 integration exposed one identity-only Checkpoint 3 integration
issue: schema 1.1 provenance still used a placeholder rewrite prompt identity.
`app.evals.integrity` now mirrors the exact frozen Checkpoint 3 prompt contract
and infrastructure settings locally, preserving generation/original-only import
isolation. Checkpoint 3's parser, cache, pending-marker, and paid-call contract
were not changed.

Mocked verification passed without live retrieval or external/model access:
`uv run pytest -q tests/unit/test_phase2_checkpoint4.py
tests/unit/test_phase2_checkpoint2.py::test_resume_rejects_query_separation_arm_mismatch_before_rewrite`
reported `24 passed`; `uv run pytest -q tests/unit` reported `315 passed` with
the same eight existing RAGAS import deprecation warnings. No smoke, 131-row
experiment, generation, Bedrock, Haiku, Anthropic, Ollama, Qdrant, sync,
indexing, network, or holdout access ran. Checkpoint 4 stops here for review; no
serving default or mechanism graduation has occurred.

## Post-Checkpoint-4 hardening follow-ups (2026-07-15)

The four Phase 2 implementation checkpoints remain accepted and closed. Before
any smoke or experiment, schema 1.1 `eval-retrieve` capture gained an all-arm
guard that rejects resolved `subquery_packaging_enabled=True` for both
`original_only` and `original_plus_rewrite`. Holdout rejection remains first,
before policy, cache, artifact, lock, rewriter, retrieval, or model access. The
guard then runs before eval-artifact creation or retrieval, while public
`run_answer()` and its serving packaging behavior are unchanged. Original-only
capture still does not import the legal rewriter or Anthropic, and the existing
rewrite-arm pipeline/context defensive guards remain in place.

The Checkpoint 3 parser's answer-prose check now treats `Yes`/`No` as prohibited
only when they begin the legal-language suffix. This accepts an already-present
identifier rendered as `Republic Act No. 9262` or `RA No. 9262`, while suffixes
beginning `No, ...` or `Yes, ...` remain literal violations. Citation injection,
literal/delimiter preservation, confidence, new-identifier, single-line, length,
and alternative-answer validation are unchanged. The cache, pending-marker, and
locking contract was not changed.

Mocked verification passed without external/model access:
`uv run pytest -q tests/unit/test_legal_query_rewriter.py
tests/unit/test_phase2_checkpoint4.py
tests/unit/test_phase2_checkpoint2.py::test_resume_rejects_query_separation_arm_mismatch_before_rewrite`
reported `52 passed`; `uv run pytest -q tests/unit` reported `321 passed` with
the same eight existing RAGAS import deprecation warnings. No smoke, five-row or
131-row experiment, generation, Haiku, Anthropic, Bedrock, OpenAI, Ollama,
Qdrant, retrieval, sync, indexing, network, or holdout access ran.
Scoped whitespace checks reported no diagnostics for the changed code, tests,
and documentation.

# Smoke and experiment execution

Do not execute until all four checkpoints pass and paid Haiku use is authorized.

## Five-row smoke

Rows:

- `eval_001` — Factual
- `eval_034` — Paraphrase
- `eval_053` — Ambiguous
- `eval_058` — out-of-scope
- `eval_124` — multi-target/synthesis-intent control

Original:

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --row-id eval_001 \
  --row-id eval_034 \
  --row-id eval_053 \
  --row-id eval_058 \
  --row-id eval_124 \
  --tag phase2-smoke-original \
  --original-only
```

Rewrite:

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --row-id eval_001 \
  --row-id eval_034 \
  --row-id eval_053 \
  --row-id eval_058 \
  --row-id eval_124 \
  --tag phase2-smoke-rewrite \
  --legal-query-separation
```

Cached repetition:

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --row-id eval_001 \
  --row-id eval_034 \
  --row-id eval_053 \
  --row-id eval_058 \
  --row-id eval_124 \
  --tag phase2-smoke-rewrite-cached \
  --legal-query-separation

uv run raglab eval-retrieval-compare \
  phase2-smoke-original \
  phase2-smoke-rewrite \
  --tag phase2-smoke-comparison
```

Require:

- no Haiku call in control;
- at most one call per cache miss;
- cached repetition is all hits;
- fallback rows match control pool/context hashes;
- one original-query rerank;
- sealed bundles;
- no generation or holdout artifact.

### Five-row smoke execution results (2026-07-15)

The authorized §3.1 smoke ran only the documented five non-holdout rows:
`eval_001`, `eval_034`, `eval_053`, `eval_058`, and `eval_124`, with the eval
profile, Qwen3-Embedding at 1024 dimensions, and MiniLM reranking. The three
retrieval bundles and comparison artifact sealed successfully under
`phase2-smoke-original`, `phase2-smoke-rewrite`,
`phase2-smoke-rewrite-cached`, and `phase2-smoke-comparison`.

The original-only control left the rewrite cache with only `.capture.lock`, and
all five frozen rewrite decisions were `disabled`, proving no control-arm Haiku
call. The first rewrite capture created exactly five durable final cache records,
no pending records, and one `miss_written` decision per row. Call latencies were
2572.45 ms (`eval_001`), 2117.51 ms (`eval_034`), 1589.94 ms (`eval_053`),
2286.45 ms (`eval_058`), and 1968.43 ms (`eval_124`). The cached repetition
reported `hit` on all five rows and created no additional cache records.

All five paid outputs were strict-parser fallbacks with
`fallback_reason=invalid_output`; no rewrite was accepted. Consequently, every
fallback row matched the control `pre_rerank_pool_hash` and
`selected_context_hash`, each row recorded exactly one rerank against the
original effective query, and the comparison reported zero pre-rerank-pool and
zero selected-context changes across five rows. No generation or holdout
artifact was created.

The smoke therefore passes its isolation, paid-call ceiling, cache, fallback
parity, rerank, sealing, and artifact-scope gates, but it provides no accepted
rewrite or two-lane quality evidence. The 131-row experiment remains unrun;
stop for review before considering any cache reset, paid retry, prompt/parser
change, or full experiment.

### One-call v1 parser diagnostic (2026-07-15)

One separately authorized direct Haiku diagnostic used the predeclared query
`What protection does RA 9262 provide?`, the existing v1 system/user prompts,
model, 160-token limit, temperature 0, and timeout. Transport retries were
explicitly disabled to enforce a one-request ceiling. The diagnostic did not
enter the answer pipeline, retrieval, rewrite cache, or any sealed artifact and
did not access holdout data. Raw response text was written only to a mode-0600
scratch file under `/private/tmp`.

The call completed in 2718.13 ms and returned 253 characters with SHA-256
`e681d53d4e4f63fe5a6581f5a1e2c0ea9f69b0141d57323026bdaedce2533813`.
An order-matched validator identified the first failed branch as
`shape.confidence_not_string`: `confidence` was a JSON number (`float`) rather
than the required string enum `"high" | "low"`. The production parser returned
`invalid`, as expected. Because validation reached this branch, this response
was not rejected for outer whitespace, line breaks, fencing/JSON syntax,
duplicate keys, root type, key set, or `legal_query` type. This single
diagnostic identifies one concrete v1 contract-compliance failure; it does not
prove that the five frozen smoke outputs failed for the same reason because
their raw text was intentionally not retained.

No retry, parser relaxation, cache mutation, or contract change followed the
diagnostic call itself.

### v2 prompt/prefill contract hardening and smoke verification (2026-07-15)

Before the next smoke, the rewrite system prompt was tightened to require one
compact single-line JSON object, the exact ordered keys and JSON types,
`citations=[]`, and string-only `"high" | "low"` confidence. It explicitly
prohibits fences, prose, outer whitespace, line breaks, and numeric confidence.
`LEGAL_REWRITE_PROMPT_VERSION` is now `"v2"`.

The Anthropic request now ends with the exact assistant prefill
`{"legal_query":"`. The API returns only the continuation; the strict parser
receives exactly `assistant_prefill + model_text_continuation`. The prefill bytes
and named reconstruction rule are explicit prompt-hash inputs alongside the
system prompt, user template, contract version, and prompt version. The lazy-safe
identity mirror in `app/evals/integrity.py` pins the same values without importing
the rewriter in original-only capture. The final prompt hash rotated from the v1
hash `1b1ebdcc28f8e4840c8ba209e59be7aea5ec36fc28f9629dfde8a6dc584f5a82`
to `0737db82638fa3624b591cfbf006a372dc74e147dcf0594683c7ae3c902ee598`.
The existing cache-key inputs therefore isolate v2 from the five retained v1
fallback records. `raw_output_hash` continues to hash only the API-returned
continuation, not the deterministic prefill.

The strict parser, cache/pending-marker/locking contract, fallback behavior,
public serving path, and general generation seam did not change. The focused
Checkpoint 3-4 verification passed 55 tests; the full unit suite passed 324 tests
with the same eight existing RAGAS import deprecation warnings. A separate
read-only Codex review found no core call-shape, reconstruction, cache, parser,
holdout-ordering, original-only-isolation, or serving-isolation defect after its
fixture-consistency finding was corrected.

The authorized v2-prefill smoke used fresh tags
`phase2-smoke-original-v2-prefill`, `phase2-smoke-rewrite-v2-prefill`,
`phase2-smoke-rewrite-v2-prefill-cached`, and
`phase2-smoke-comparison-v2-prefill`. The fresh control sealed five disabled
decisions without adding a cache record. The rewrite pass made five new v2
misses with distinct continuation hashes and no pending residue. Call latencies
were 2303.69 ms (`eval_001`), 1426.79 ms (`eval_034`), 1316.61 ms
(`eval_053`), 1814.40 ms (`eval_058`), and 1581.44 ms (`eval_124`).

`eval_001` and `eval_034` were accepted at high confidence and exercised the
two-lane dense/sparse/fusion path. Both changed their pre-rerank pool and selected
context. `eval_053`, `eval_058`, and `eval_124` reached content validation and
fell back with `literal_violation`; all three retained byte-exact control pool
and context hashes. Every row was reranked exactly once against the original
query. The cached repetition returned five hits with no new call or cache record.
All three retrieval bundles validated sealed, comparison reported 2/5 pool and
2/5 context changes exactly on the accepted rows, and no generation or holdout
artifact was created. The cache now contains the five retained v1 records plus
five v2 records and no pending marker.

The v2 smoke therefore clears the format-contract and accepted-path gate while
preserving strict content fallbacks. The 131-row experiment remains unrun and
requires a separate review decision; no cache deletion, retry, parser relaxation,
or serving change is implied.

### Predeclared v3 prompt and gate-7 amendment (before v3 smoke)

The v2 five-row smoke showed that one aggregate fallback-rate gate conflates
broken plumbing with intentional content safety. Operational failures
(`timeout`, `llm_error`, `invalid_output`, and `interrupted_after_request`) mean
the paid-call mechanism failed. Content decisions (`literal_violation` and
`low_confidence`) mean the strict safety contract declined to activate a rewrite;
that can be desirable on out-of-scope rows but excessive on the target slice.

Before any v3 smoke or 131-row result, gate 7 is therefore replaced with two
predeclared checks:

- Operational fallbacks must be at most 6 of the 131 rewrite decisions (no more
  than 5%). Classification uses each row's durable `fallback_reason` regardless
  of whether the full capture obtains it as a fresh miss or a cache hit, so an
  earlier cached failure cannot disappear from the denominator.
- At least 24 of the 31 pooled Paraphrase/Ambiguous rows must have
  `status=accepted`, `parser_outcome=valid`, and `confidence=high` (at least 75%).
  `literal_violation` and `low_confidence` rows on this target slice count against
  this acceptance floor even though they are not operational failures.

Out-of-scope content fallbacks are not operational failures and remain evaluated
by gate 6's abstention/context moat. Gate 8 separately retains the stricter zero
`interrupted_after_request` requirement. This amendment is based only on the
designated five-row smoke and is frozen before the v3 smoke and full experiment;
it must not be adjusted after seeing the 131-row result.

The v3 prompt changes no parser rule. It adds one generation instruction: never
mention any statute number, act number, article number, section number, or
case/docket number in the rendering; describe the legal doctrine or concept in
words instead. The prompt version and prompt hash must rotate, with v1 and v2
cache records retained and isolated. The v3 smoke must rerun the same five rows
under fresh tags before the 131-row run is considered.

### v3 implementation and five-row smoke result (2026-07-15)

The prompt-only amendment was implemented as specified. Runtime and the lazy
schema identity mirror now use `prompt_version="v3"` and prompt hash
`a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91`.
The v2 assistant prefill, reconstruction rule, request parameters, strict parser,
continuation-only raw hash, cache/pending/locking behavior, original-only import
isolation, serving path, and retrieval behavior did not change. Tests prove that
the real v1 and v2 cache keys cannot hit v3. Focused verification passed 56 tests;
the full unit suite passed 325 tests with the same eight RAGAS deprecation
warnings. A separate read-only Codex review reported no findings.

The authorized five-row run used fresh tags
`phase2-smoke-original-v3-doctrine`, `phase2-smoke-rewrite-v3-doctrine`,
`phase2-smoke-rewrite-v3-doctrine-cached`, and
`phase2-smoke-comparison-v3-doctrine`. The control sealed five disabled decisions
without adding a cache record. The rewrite pass wrote five v3 misses with
distinct continuation hashes, no pending residue, and latencies of 1904.00 ms
(`eval_001`), 1358.62 ms (`eval_034`), 1450.95 ms (`eval_053`), 1379.42 ms
(`eval_058`), and 1234.78 ms (`eval_124`). Cached repetition returned five hits
without a new call or record.

`eval_034` (Paraphrase) and `eval_053` (Ambiguous) were accepted, valid,
high-confidence rewrites; both exercised the two-lane path and changed both the
pre-rerank pool and selected context. `eval_001` (Factual), `eval_058` (OOS), and
`eval_124` (Synthesis control) fell back with `literal_violation` and retained
byte-exact control pool/context hashes. The raw text is intentionally hash-only,
so the exact content branch within `literal_violation` cannot be diagnosed from
the artifact. Every row had exactly one original-query rerank, all three bundles
validated sealed, and the comparator reported 2/5 pool and 2/5 context changes
exactly on the accepted rows. No generation or holdout artifact was created.

V3 did not raise aggregate smoke acceptance above 2/5, but it moved acceptance
onto both rows representing the 31-row pooled target slice: target-slice smoke
activation improved from 1/2 under v2 to 2/2 under v3. The three remaining
content fallbacks include the desirable OOS non-activation and introduce zero
operational failures under the predeclared gate-7 classification. The cache now
contains five records for each of v1, v2, and v3 with no pending marker. This is
a positive pre-experiment signal, not a graduation result; the 131-row run
remains unrun pending separate authorization, and the predeclared gates remain
unchanged.

## Full 131-row MiniLM experiment

```bash
RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --tag phase2-original-minilm \
  --original-only

RAGLAB_PROFILE=eval RERANKER_BACKEND=minilm \
uv run raglab eval-retrieve \
  --split regression --split dev \
  --tag phase2-legal-rewrite-minilm \
  --legal-query-separation

uv run raglab eval-retrieval-compare \
  phase2-original-minilm \
  phase2-legal-rewrite-minilm \
  --tag phase2-legal-rewrite-minilm-comparison
```

Report:

- provision/leaf Hit@1, 3, 5, 8;
- recall and MRR;
- survival through dense, sparse, fused, reranked, expanded, selected;
- original versus rewrite discovery;
- Paraphrase/Ambiguous separately and pooled;
- Factual/Synthesis regressions;
- out-of-scope hard abstention and context behavior;
- stage candidate counts;
- mean/p95 retrieval and rewrite latency;
- rerank input size;
- rewrite acceptance/cache/fallback rates;
- pool/context changes by row;
- manual-review list for every changed context.

# Graduation gates

All must pass:

1. Among 31 pooled Paraphrase/Ambiguous rows, selected exact-provision Hit@8
   improves by at least five percentage points and two rows.
2. Pooled exact-leaf Hit@8 and leaf MRR do not decline.
3. Reranked-to-selected target survival does not decline on the target slice.
4. Among 70 Factual rows, aggregate Hit@8 declines by no more than 1.5 points
   and at most one row regresses.
5. Among 18 Synthesis rows, aggregate Hit@8 does not decline; at most one
   individual loss is allowed only if offset.
6. Among 12 out-of-scope rows, hard abstentions do not decrease and mean
   selected-context count rises no more than 10%.
7. Operational fallback and target activation both pass:
   - at most 6 of 131 rewrite decisions have `fallback_reason` in
     `{timeout, llm_error, invalid_output, interrupted_after_request}`; and
   - at least 24 of 31 pooled Paraphrase/Ambiguous rows are accepted, valid,
     high-confidence rewrites.
8. Clean full run has zero `interrupted_after_request`.
9. Cached smoke repetition is 100% hits with zero paid calls.
10. Retrieval p95 increases no more than three seconds.
11. Rerank input never exceeds the deterministic two-lane bound.
12. All schema/provenance/corpus/index checks pass.
13. Manual review finds no lost literal, injected citation, invented fact, or
    broadened question.

## Full 131-row MiniLM experiment result (2026-07-15)

The authorized matched regression/dev experiment completed in the declared
order under `RAGLAB_PROFILE=eval` and `RERANKER_BACKEND=minilm`, using the three
commands above and no generation, holdout, sync, indexing, RAGAS, serving, or
rollback action. Before execution, one paid-call ceiling defect was found in
the otherwise accepted Checkpoint 3 implementation: the Anthropic SDK defaults
to two transport retries. `_call_haiku()` now passes `max_retries=0`, and its
focused test pins that argument. This is implementation hardening, not a prompt,
parser, cache, or experiment-policy change.

Preflight resolved Qwen3-Embedding (`qwen3-embedding:0.6b`) at 1024 dimensions,
collection `ph_law_qwen06`, MiniLM reranking, and both query decomposition and
subquery packaging disabled. Runtime and schema both resolved prompt version
`v3` and prompt hash
`a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91`.
The cache began with exactly five final records for each of v1, v2, and v3 and
no pending marker. No concurrent `raglab` sync, retrieval, or indexing process
was active, and all full-run tags were absent. The focused Checkpoint 3-4 suite
reported `56 passed in 10.79s`; `uv run pytest -q tests/unit` reported
`325 passed, 8 warnings in 17.25s`, with the same eight RAGAS import deprecation
warnings. The test runs used restricted networking, explicit forbidden-call
test seams, and fake Anthropic clients.

Both retrieval bundles sealed with 131 rows in identical order. The control has
131 `original_only`/`disabled`/`bypassed` decisions and did not change the
cache. The rewrite arm has 73 accepted rows and 58 byte-exact fallback rows:
47 `literal_violation`, 11 `low_confidence`, and zero operational fallbacks.
All fallback canonical pools and selected contexts equal their control rows.
Every accepted row has original and legal-rewrite dense/sparse/fused lanes plus
one canonical combined fused pool. Every row has one canonical combined-pool
rerank against the original query. The largest main rerank input was 60, below
the deterministic bound of 80. The five v3 smoke keys were hits; the other 126
rows were `miss_written`,
so the run made exactly 126 paid requests. The cache ended with 141 finals
(v1=5, v2=5, v3=131) and no pending residue. Transport retries were disabled.

The comparator sealed 131 rows and reports 73 pool-hash and 73 selected-context-
hash changes, exactly the accepted rows. Fifty-four of those 73 nevertheless
retain byte-identical selected chunk IDs, order, and text; score/provenance
metadata changed their hashes. Nineteen rows have an actual selected-content
change. Bundle validation found no generation or holdout artifact and no
storage drift during either capture.

### Frozen identities and artifact provenance

- corpus: `9e08b2bf37d0e5bda502afd11312b17c9418d4941d346cae60e7f40ec8bee7eb`
- index: `debec6abc3bbf7dc095a794da4878b9da4940aeff58fb3a428d86a1cab9677c6`
- BM25: `c2bae978f01d61d5395a358d695d256217e88e5d1175596f8fa224897346065f`
- Qdrant: `1d77f4c9529b3d71118e8d51ec4d55f7cd17eae7fa726de26546839e5b0cc2ae`
- ordered dataset rows: `70dc3a6860ad8dbc5c38de6a627091e9a660fbafabf36630c88e618eec3ef302`
- ordered targets: `863f97ea948ac0444bb6acc2624bdd2c2a0ae889d219c7f393117b18efe2e179`
- shared comparison identity: `f94a1a7e5007c0093a51c01b360d8d010981fb394fd11f9e4c50640b030b158d`
- control full/bundle hashes:
  `d02aa18816c6b68b8c8bbea07a8966dc6ef8a6a0390f02ac9f37ef6c3f59a331` /
  `6ef83e8e77cf2959173e5d33c5993082962329caf4ff6c8718c2e38955d0031c`
- rewrite full/bundle hashes:
  `c59762674333789d814921f1676b7841157fdeed9302fad46440a6a877a494af` /
  `e4d0bfc64ad36282cb3611b482c24b7c93f539dbe13fca31529fca77b7fc2319`
- comparison report: `fd984dc90f762f2455a75e82a7ca8549550c14e8646586971a8b89d873418ea1`

Schema 1.1, corpus, index, BM25, Qdrant, dataset, targets, embeddings,
reranker, cutoffs, selection, evidence, prompt, and semantic-input identities
all match. Capture-consistency checks also match with no corpus/index mutation.

### Aggregate provision and leaf retrieval metrics

Each cell is control → rewrite. Recall is shown at 1/3/5/8.

| Stage | Provision Hit@1/3/5/8 | Provision recall@1/3/5/8 | P-MRR | Leaf Hit@1/3/5/8 | Leaf recall@1/3/5/8 | L-MRR |
|---|---|---|---|---|---|---|
| dense | .6034/.7586/.8190/.8707 → same | .5050/.6588/.7292/.7852 → .5050/.6588/.7335/.7895 | .7032→.7035 | .5000/.6923/.7308/.8462 → same | .5000/.6731/.7115/.8462 → same | .6141→.6141 |
| sparse | .3966/.5862/.6379/.6983 → same | .3125/.5057/.5517/.6106 → same | .4961→.5041 | .3846/.6154/.6154/.6923 → same | .3654/.5962/.6154/.6923 → same | .4863→.4898 |
| fused | .5345/.7414/.7931/.8276 → .5431/.7672/.8276/.8707 | .4497/.6487/.7004/.7421 → .4497/.6616/.7292/.7852 | .6509→.6655 | .5385/.6923/.7692/.7692 → .5769/.6538/.6923/.8077 | .5192/.6923/.7692/.7692 → .5577/.6538/.6923/.8077 | .6194→.6380 |
| reranked | .5259/.7241/.8017/.8448 → .5259/.7328/.8103/.8534 | .4504/.6264/.7011/.7385 → .4504/.6307/.7055/.7428 | .6457→.6497 | .5000/.6154/.6923/.7308 → same | .4808/.6154/.6923/.7308 → same | .5887→.5865 |
| expanded | .5259/.7328/.8190/.8448 → .5259/.7328/.8276/.8534 | .4504/.6307/.7083/.7385 → .4504/.6307/.7126/.7428 | .6405→.6440 | .3462/.5000/.5385/.5769 → .3462/.4615/.5000/.5385 | same as Hit | .4292→.4163 |
| selected | .5259/.7328/.8190/.8448 → .5259/.7328/.8276/.8534 | .4504/.6307/.7083/.7385 → .4504/.6307/.7126/.7428 | .6405→.6440 | .3462/.5000/.5385/.5769 → .3462/.4615/.5000/.5385 | same as Hit | .4292→.4163 |

Full-stage provision/leaf recall is dense `.8599/.8462 → .8685/.8462`,
sparse `.6430/.6923 → .7205/.7308`, fused `.8556/.8462 → .8685/.8462`,
reranked `.8599/.8462 → .8642/.8462`, and expanded/selected
`.7385/.5769 → .7428/.5385`. Relative to each arm's dense+sparse target union,
provision survival is fused `1.0→1.0`, reranked `.8529→.8542`, and
expanded/selected `.8529→.8542`; leaf survival is fused `1.0→1.0`, reranked
`.8636→.8636`, and expanded/selected `.6818→.6364`.

### Declared category slices and discovery

For the 31 pooled Paraphrase/Ambiguous rows, selected provision Hit@8 remains
25/31 (`.8065→.8065`), with no gains or losses. Selected provision MRR is
`.5240→.5240`. Exact-leaf Hit@8 falls from 6/11 (`.5455`) to 5/11
(`.4545`), and leaf MRR from `.3212` to `.2909`; `eval_126` is the lost frozen
leaf match. Reranked-to-selected primary-target retention remains `1.0→1.0` on
the 25 applicable rows.

Paraphrase selected provision Hit@1/3/5/8 is
`.4000/.5200/.6400/.7600→same`, recall
`.3400/.4600/.5533/.6267→same`, and MRR `.4964→.5030`. Its eight applicable
leaf rows move from `.125/.500/.625/.625`, MRR `.3167`, to
`.125/.375/.500/.500`, MRR `.2750`. Ambiguous selected provision Hit is
`.3333/1/1/1→same`, recall `.3333/.9167/.9167/.9167→same`, and MRR
`.6389→.6111`; its three leaf rows remain `.3333` at every cutoff and MRR.

Factual primary selected Hit@8 is 60/70 (`.8571→.8571`) with no row gain or
loss. On 68 exact-provision-applicable rows, Hit@8 remains `.8529`, Hit@3 moves
`.7941→.7794`, and MRR `.7061→.7049`. Synthesis primary selected Hit@8 improves
16/18 (`.8889`) to 17/18 (`.9444`) through the sole gain `eval_050`, with no
loss. Its 17 applicable provision rows move Hit@8 `.8824→.9412` and MRR
`.5902→.6196`; leaf metrics do not change.

Original-versus-rewrite discovery, defined over either dense or sparse lane,
is: among 119 applicable rows, 60 both, 51 original-only, one rewrite-only
(`eval_050`), and seven neither. On the pooled 31 target rows it is 20 both,
nine original-only, zero rewrite-only, and two neither. Paraphrase is 15/8/0/2
and Ambiguous 5/1/0/0 in the same order. Among 11 pooled leaf-applicable rows it
is seven both, two original-only, zero rewrite-only, and two neither.

All 12 OOS rows remain non-abstentions in both arms: hard abstentions are
`0→0`. Mean selected count is `5.3333→5.4167` (+1.5625%); only `eval_069`
changes count, from seven to eight. This passes the frozen numerical gate but
continues to expose selected-context answer-leak risk on every OOS row.

### Operations, latency, and candidate bounds

Decision counts by category are: Ambiguous 5 accepted/1 low-confidence;
Factual 30 accepted/34 literal-violation/6 low-confidence; OOS 9 accepted/3
literal-violation; Paraphrase 16 accepted/8 literal-violation/1 low-confidence;
Synthesis 13 accepted/2 literal-violation/3 low-confidence. Cache hits/misses by
category are Ambiguous 1/5, Factual 2/68, OOS 1/11, Paraphrase 1/24, and
Synthesis 0/18.

Control versus rewrite retrieval latency is mean `1049.44→1245.45 ms` and p95
`2019.15→2204.69 ms`. Durable rewrite-call latency across all decisions is mean
`1962.46 ms`, p95 `4970.07 ms`; the 126 new misses alone are mean `1982.18 ms`,
p95 `4975.28 ms`. The artifact does not separately time cache lookup, so no
cache-hit rewrite-latency claim is made.

Main rerank input size is mean `28.473→32.603`, p95 `38.5→50`, and maximum
`40→60`, within the bound of 80. Mean control→rewrite stage candidate counts are
dense `23.183→34.397`, sparse `9.924→15.496`, fused `28.473→32.603`, reranked
`30.947→34.992`, expanded `6.328→6.336`, and selected `6.328→6.328`. Original-
lane dense/sparse/fused means stay `23.183/9.924/28.473`; legal-lane means over
all 131 rows are `11.214/5.573/14.046`. Mean dense/sparse/rerank/expand/select
stage latency changes from `246.65/118.13/502.16/213.40/.51 ms` to
`349.35/139.62/576.87/207.95/.49 ms`.

### Frozen graduation gates

| Gate | Outcome | Evidence |
|---|---|---|
| 1 | **FAIL** | Pooled provision Hit@8 `.8065→.8065`; zero gains, not +5 points/two rows. |
| 2 | **FAIL** | Pooled leaf Hit@8 `.5455→.4545`, MRR `.3212→.2909`; `eval_126` lost. |
| 3 | PASS | Target-slice reranked-to-selected retention `1.0→1.0`. |
| 4 | PASS | Factual primary Hit@8 `.8571→.8571`; zero losses. |
| 5 | PASS | Synthesis `.8889→.9444`; `eval_050` gain, zero losses. |
| 6 | PASS | OOS hard abstention `0→0`; mean context count +1.5625%. |
| 7 | **FAIL** | Reliability passes at 0 operational fallbacks, but target activation is 21/31, below 24/31. |
| 8 | PASS | Zero `interrupted_after_request`. |
| 9 | PASS | Predeclared v3 cached smoke was 5/5 hits with zero paid calls; it was not rerun. |
| 10 | PASS | Retrieval p95 increased 185.54 ms, below 3 seconds. |
| 11 | PASS | Maximum rerank input 60, below deterministic bound 80. |
| 12 | PASS | All schema, provenance, corpus, index, dataset, target, config, and capture identities match. |
| 13 | **FAIL** | Manual review found broadened/altered or invented legal criteria on 19 rows. |

### Gate-13 changed-context manual review

All 73 accepted/hash-changed rows preserve the literal original question
byte-for-byte as a prefix, and none introduces a formal identifier or citation.
The strict prefix rule nevertheless permits an appended legal rendering to
broaden or alter the question or assert unsupported legal criteria. Review
classified 19 such rows. Actual context effects were three helpful, ten harmful,
and 60 neutral. One target provision was gained (`eval_050`), no target provision
ID was lost, one frozen leaf target was lost (`eval_126`), and `eval_039` lost
the fuller parent coverage while retaining its provision ID.

| Row | Literal / ID | Rewrite-content assessment | Context effect and target change |
|---|---|---|---|
| 002 | preserved / none | faithful | neutral; byte-exact context |
| 003 | preserved / none | faithful | neutral; byte-exact |
| 005 | preserved / none | faithful | neutral; byte-exact |
| 006 | preserved / none | faithful | neutral; byte-exact |
| 007 | preserved / none | faithful | neutral; byte-exact |
| 009 | preserved / none | faithful | neutral; byte-exact |
| 010 | preserved / none | faithful | minor harm; swaps irrelevant counterfeit-note text for trademark-fraud text |
| 012 | preserved / none | faithful | neutral; byte-exact |
| 013 | preserved / none | broadens annulment to declaration of nullity | neutral context swap |
| 014 | preserved / none | faithful | neutral; byte-exact |
| 016 | preserved / none | faithful | neutral; byte-exact |
| 017 | preserved / none | faithful | neutral; byte-exact |
| 024 | preserved / none | invents/broadens controlled precursors and chemicals | neutral; byte-exact §11 context |
| 025 | preserved / none | changes dangerous-drug sale to precursor trafficking | harm; loses confiscation context and gains irrelevant manufacture text |
| 026 | preserved / none | faithful | neutral; byte-exact |
| 029 | preserved / none | faithful | neutral; byte-exact |
| 030 | preserved / none | faithful | neutral; byte-exact |
| 032 | preserved / none | faithful | neutral; byte-exact |
| 034 | preserved / none | faithful | neutral; byte-exact |
| 035 | preserved / none | faithful | neutral; byte-exact |
| 036 | preserved / none | faithful | neutral; byte-exact |
| 037 | preserved / none | faithful | neutral; byte-exact |
| 039 | preserved / none | faithful | harm; full §11 parent replaced by leaf plus irrelevant §28; provision ID retained |
| 041 | preserved / none | adds declaration of nullity based on fraud | neutral context |
| 042 | preserved / none | faithful | neutral; byte-exact |
| 043 | preserved / none | broadens sale/illegal drugs to distribution/controlled substances | neutral; byte-exact |
| 044 | preserved / none | adds quasi-delict as a distinct basis | neutral; byte-exact |
| 045 | preserved / none | adds a separate right-to-remain-silent issue | neutral; byte-exact |
| 046 | preserved / none | faithful | help; removes irrelevant estafa text; missing Civil target remains missing |
| 048 | preserved / none | adds who may seek annulment | neutral; byte-exact |
| 049 | preserved / none | faithful | neutral; byte-exact |
| 050 | preserved / none | faithful | help; gains Revised Penal Code article 309 target |
| 052 | preserved / none | faithful | harm; adds irrelevant Civil Code article 1599, target retained |
| 053 | preserved / none | faithful | neutral; frozen leaf mismatch remains |
| 054 | preserved / none | changes “without telling” to consent/notification doctrine | help; removes irrelevant article 43 |
| 056 | preserved / none | faithful | harm; replaces relevant Statute-of-Frauds text with irrelevant prescription text |
| 057 | preserved / none | invents “danger to the community” as bail criterion | harm; adds irrelevant context |
| 059 | preserved / none | narrows taxpayer question to citizenship/residency | neutral; still no tax-law context |
| 060 | preserved / none | faithful OOS | neutral; no corporate-law answer |
| 061 | preserved / none | faithful OOS | neutral; no tax answer |
| 062 | preserved / none | faithful OOS | neutral; no procedural answer |
| 064 | preserved / none | faithful OOS | neutral; no SSS answer |
| 065 | preserved / none | faithful OOS | neutral; no customs answer |
| 067 | preserved / none | drops Quezon City and broadens to all local governments | neutral; no rules answer |
| 068 | preserved / none | faithful OOS | neutral; no securities answer |
| 069 | preserved / none | invents an all-parties-consent rule | harm; partial privacy context may mislead |
| 070 | preserved / none | faithful | neutral; byte-exact |
| 071 | preserved / none | faithful | neutral; byte-exact |
| 073 | preserved / none | faithful | neutral; byte-exact |
| 074 | preserved / none | invents unsupported evidentiary/procedural changes | neutral; byte-exact |
| 078 | preserved / none | adds pardon, distinct from the asked parole issue | neutral; byte-exact |
| 079 | preserved / none | faithful | harm; drops useful trafficking provisions for agency/extraterritorial text; target retained |
| 083 | preserved / none | faithful | neutral; byte-exact |
| 085 | preserved / none | misstates mitigating/aggravating role for the minimum indeterminate term | neutral; byte-exact |
| 087 | preserved / none | faithful | neutral; byte-exact |
| 089 | preserved / none | faithful | neutral; byte-exact |
| 090 | preserved / none | faithful | neutral; constitutional target absent in both arms |
| 091 | preserved / none | faithful | harm; adds large irrelevant registration/definition context; repeal target retained |
| 094 | preserved / none | adds unsupported “family courts” framing | neutral; byte-exact |
| 098 | preserved / none | faithful | harm; adds child-offender text contrary to the adult scenario; targets retained |
| 103 | preserved / none | invents administrative penalties | neutral; byte-exact |
| 105 | preserved / none | faithful | neutral; byte-exact |
| 106 | preserved / none | faithful | neutral; swaps nonresponsive chunks |
| 113 | preserved / none | faithful | neutral; byte-exact |
| 114 | preserved / none | faithful | neutral; byte-exact |
| 115 | preserved / none | faithful | neutral; byte-exact |
| 119 | preserved / none | faithful | neutral; byte-exact |
| 120 | preserved / none | conflates battered-woman syndrome and incomplete self-defense | neutral; byte-exact |
| 121 | preserved / none | faithful | neutral; byte-exact |
| 122 | preserved / none | faithful | neutral; byte-exact |
| 123 | preserved / none | faithful | neutral; byte-exact |
| 126 | preserved / none | faithful | neutral content; focused leaf replaced by full parent containing the rule; frozen leaf lost |
| 131 | preserved / none | faithful | neutral; constitutional target absent in both arms |

Because gates 1, 2, 7, and 13 fail, legal-query separation does not graduate.
Generation replay is not authorized by these results and was not run. Serving
remains original-only; no rollback implementation or new prompt version was
created.

# Later generation replay

Only after retrieval gates pass:

```bash
uv run raglab eval-generate \
  phase2-original-minilm \
  --tag phase2-original-minilm-gemma4 \
  --generator-model gemma4:e4b

uv run raglab eval-generate \
  phase2-legal-rewrite-minilm \
  --tag phase2-legal-rewrite-minilm-gemma4 \
  --generator-model gemma4:e4b
```

Generation safety requires:

- no new answer leaks on out-of-scope rows;
- no increase in false abstentions;
- target-slice faithfulness/context recall decline no greater than 0.01;
- no new warning class beyond the eight known RAGAS warnings;
- manual review of all changed-context target rows.

Passing does not automatically change serving defaults. Any default flip needs
separate approval.

# Expected files

- `app/retriever/legal_query_rewriter.py` — new
- `app/retriever/hybrid_retriever.py`
- `app/retriever/context_selection.py`
- `app/retriever/strategy.py`
- `app/pipeline/state.py`
- `app/pipeline/stages.py`
- `app/pipeline/runner.py`
- `app/observability/context.py`
- `app/evals/frozen_contexts.py`
- `app/evals/integrity.py`
- `app/evals/retrieval_runner.py`
- `app/evals/retrieval_trace.py`
- `app/evals/retrieval_metrics.py`
- `app/evals/retrieval_comparison.py` — new
- `app/cli/main.py`
- `app/config.py`
- `.env.example`
- focused unit tests
- `docs/retrieval_strategy_review.md`
- `docs/project_plan.md`
- external devlog

No new dependency is expected. No database, API, frontend, `eval_store`, or ADR
change is authorized.

## Phase 4 holdout validation executor implementation (2026-07-17)

The Phase 4 holdout-validation executor is implemented behind default-off
adaptive context packaging. No holdout row was accessed for this implementation
pass, and no serving default, ADR, or schema 1.2 change was made.

Implemented surfaces:

- live adaptive seam after post-expansion dedup, default off, wired through
  Settings → RetrievalKnobs → policy/config identity → active config → trace
  diagnostics;
- selector-consumed semantic pool hash and diagnostic full pool hash over the
  pre-adaptive, post-dedup packaging pool;
- non-holdout CP-A0 command with the locked five sentinel rows
  (`eval_075`, `eval_129`, `eval_053`, `eval_039`, `eval_055`) and aggregate
  bridge selection only;
- aggregate-only paired Phase 4 comparator that scores both arms quietly, joins
  internally, validates identities and per-row semantic pool invariants, emits
  only aggregate gates/distributions, and logs exactly one holdout metric read
  when used on holdout tags;
- non-disclosure unit coverage asserting no eval ID, question, category, answer,
  context, or row metric appears in paired stdout/artifacts/ledger extras.

Locked holdout gates remain:

- common-answered faithfulness Δ and context-recall Δ each pass at ≥ -0.01;
  -0.02 ≤ Δ < -0.01 is inconclusive and does not graduate;
- false abstentions must not increase;
- all-row rendered-token reduction must be ≥ 0.05;
- empty cohort, missing metric, identity drift, per-row semantic pool mismatch,
  or unapproved config difference fails closed.

CP-A0, CP-A2, CP-A3 dev validation, and Stage B holdout execution are still
separate operational steps. CP-A0 must be recorded here with its actual bridge
selection before any holdout access. Stage B uses a single retrieval pass per row
and derives both fixed and adaptive contexts from that same post-dedup pool,
eliminating cross-run retrieval score jitter as a possible holdout blocker while
preserving the same aggregate-only comparator and single holdout metric read.

## Checkpoint 2 implementation results (2026-07-15)

Checkpoint 2 is implemented and stops before legal rewriting or two-lane
retrieval. New frozen retrieval records use schema 1.1 with the required
original lane plus one non-mutating `fused/combined` canonical pre-rerank pool;
schema-minor-dispatched validation and replay retain sealed 1.0 support while
rejecting unsupported minors and missing/duplicate 1.1 pools. Aggregate fused
metrics/counts/hashes use only the canonical pool, and dense, sparse, and fused
lane metrics are reported by query variant without clone double counting.

Retrieval provenance now separates shared and query-separation identity, with
full-identity resume checks and a 1.0 original-only adapter. The new
`eval-retrieval-compare` command validates both sealed non-holdout bundles and all
matched retrieval identities before atomically publishing per-row pool/context
changes; import-isolation tests confirm it does not load retrieval, generation,
embedding, reranker, Anthropic, or Haiku modules. Focused tests passed without
live retrieval, generation, paid calls, or holdout access. No experiment has run
and no mechanism has graduated.


### Phase 4 Holdout Validation CP-A0

- Sentinel count: 5
- Matched count: 5
- Mismatched count: 0
- Binding bridge: full-path
- Locked gates: faithfulness/context-recall Δ ≥ -0.01; false abstentions must not increase; rendered-token reduction ≥ 0.05.

### Phase 4 Holdout Validation CP-A2

- CP-A2.b selector-logic equivalence: passed, 131/131 non-holdout rows matched
  `phase4-adaptive-context-v2-minilm` selected results and required diagnostics
  (`signals`, `cap`, `stop_reason`, `rendered_tokens`).
- CP-A2.c live-on semantic equivalence: re-stated at selector-semantic
  granularity after diagnosing the score-inclusive artifact hash. The
  `raglab eval-phase4-cp-a2c` run at `2026-07-17T09:30:53.895050+08:00`
  passed with 131/131 semantic matches and 0 semantic failures against
  `phase4-adaptive-context-v2-minilm`. The score-inclusive full hash is retained
  as a diagnostic only; this run reported 0 score-inclusive mismatches.
- Diagnostic limitation: CP-A0 matched the five locked sentinels and selected the
  full-path bridge, but `eval_001` demonstrates that live retrieval metadata can
  drift at `_retrieval_scores.dense_score` without changing selector-consumed
  content. The load-bearing guarantee for Stage B is therefore the semantic pool
  invariant, not score-inclusive full-hash reproduction.
- Stage B design decision before holdout access: use the single-retrieval-pass
  Phase 4 runner. For each row, retrieve once with adaptive off, freeze the
  pre-adaptive post-dedup pool, derive the baseline as the full pool, derive the
  candidate through `select_adaptive_context`, generate both arms, score quietly,
  and pass both arms to the same CP-A3 aggregate comparator. This makes the
  cross-arm `packaging_pool_semantic_hash` invariant hold by construction and
  avoids spending the holdout read on two independent live retrieval passes.
- Implementation hardening before CP-A3: the single-pass runner passes
  `floor`, `base_cap`, `uncertain_cap`, `multifacet_cap`,
  `stabilization_patience`, and `token_target` explicitly from
  `policy.retrieval_defaults` into `select_adaptive_context`, so Stage B
  validates the same active retrieval knobs the live seam would use.
- CP-A3 dev aggregate: after confirming local Ollama `0.32.1` and installed
  `gemma4:e4b` (`c6eb396dbd59`), the non-holdout single-pass run completed as
  `phase4-single-pass-dev-cp-a3-20260717-1010`. The paired comparator exercised
  its identity, scoring-identity, storage-consistency, semantic-invariant, and
  non-disclosure checks and published aggregate artifact
  `data/eval_results/phase4_paired/phase4-single-pass-dev-cp-a3-20260717-1010.json`.
  Row count was 131 and common-answered `n` was 115.
- Non-disclosure guard hardening: category disclosure is now checked by exact
  emitted scalar value instead of substring, while eval IDs, questions, answers,
  ground truth, and contexts remain substring-checked. This avoids false
  positives from required aggregate names such as `synthesis_detected` while
  still failing closed if a category value itself is emitted.
- CP-A3 gates all passed on dev: faithfulness Δ = -0.008280970238260799
  (`pass`), context-recall Δ = 0.002898550725217386 (`pass`), false abstentions
  improved 5→4 (`pass`), and rendered-token reduction was
  0.1164511165838384 (`pass`). Changed-context count was 66; candidate caps were
  91 rows at cap 7 and 40 rows at cap 11; candidate stop distribution was
  cap=41, exhausted=80, stabilized=5, token_target=5.
- Stage B single holdout read completed as
  `phase4-single-pass-holdout-stage-b-20260717-1200` with aggregate artifact
  `data/eval_results/phase4_paired/phase4-single-pass-holdout-stage-b-20260717-1200.json`.
  The holdout ledger contains exactly one aggregate metric-read entry for this
  run. No holdout row content was inspected or disclosed.
- Stage B hard gates all passed on the 30-row sealed holdout: common-answered
  `n` was 29; faithfulness Δ = 0.022550629444827552 (`pass`); context-recall
  Δ = -0.005747126437931072 (`pass`); false abstentions did not increase
  (1→1, `pass`); rendered-token reduction was 0.1376842483117582 (`pass`).
  The final verdict is `eligible_for_release_decision`.
- Stage B execution aggregates: changed-context count 10; candidate caps were
  22 rows at cap 7 and 8 rows at cap 11; candidate stop distribution was
  cap=2, exhausted=21, stabilized=5, token_target=2. Signal activations were
  coverage_uncertain=3 and synthesis_detected=5 in both arms; labeled synthesis
  evidence remains n=4.

### Phase 4 graduation decision (2026-07-17)

ADR-027 accepts adaptive final-context packaging as the retrieval default.
`adaptive_context_enabled` now defaults to `true`; rollback is one flag,
`adaptive_context_enabled=false`.

The release decision rests on the full equivalence and validation chain:

- CP-A0 locked sentinel bridge: 5/5 matched.
- CP-A2.b pure selector logic: 131/131 non-holdout rows matched.
- CP-A2.c live-on semantic selection: 131/131 non-holdout rows matched.
- CP-A3 non-holdout single-pass aggregate: all hard gates passed and reproduced
  the offline v2 evidence.
- Stage B holdout: single aggregate read, all hard gates passed, verdict
  `eligible_for_release_decision`.

Known caveats are carried into ADR-027 and remain operational watch points:
small holdout N, context-recall dipped by -0.005747126437931072 while staying
inside the locked band, the OOS moat was not re-tested by holdout, labeled
synthesis evidence is n=4, and the mechanism changed 10/30 holdout contexts.

No schema-1.2 or explicit `packaging_pool` publication is included in this
graduation. Those are follow-up packaging/provenance work, not prerequisites for
the default flip.

# Phase 5 plan: Corrective retrieval with global rerank (2026-07-17)

**Status:** CP1 passed and CP2 is implemented in an eval-only arm (commit
`0f6bf79`). CP3 is retrieval-only and remains pending its sealed run; its
predeclared gates below are authoritative alongside the standalone corrective
checkpoint record.

**Thesis under test:** corrective retrieval works as candidate discovery
feeding one global rerank plus Phase 4 adaptive packaging, not as a context
append. PR5c's flat-to-negative result indicted the append packaging; Phase 5
is the re-test of the facet checker with that packaging replaced.

## Reconciliation with pipeline-refactor PR5 (signed off 2026-07-17)

PR5 built three separable things; Phase 5 replaces only the third:

| PR5 component | Phase 5 fate |
|---|---|
| Facet checker (`evidence_gate="crag"`, Haiku, fail-open, `sufficient`/`partial`, never abstains) | Survives as the corrective trigger and the source of `missing_facets`. |
| Per-facet targeted retrieval against the curated corpus | Survives as candidate discovery; output redirected into the union pool instead of an append. |
| Additive packaging (`_relevant_to_question` margin filter, post-gate append via `round_robin_merge`, `corrective_max_added` budget) | Retired. Never rerun — this is the retrieval-design bug (eval_102). |

One PR5 design decision is explicitly reversed, with sign-off: §10.1's
additive invariant ("corrective never removes a baseline chunk") is dropped.
Under global rerank plus adaptive packaging, a corrective candidate can
displace a baseline chunk — that is the point of the redesign. The invariant's
no-new-abstain purpose survives untouched: `min_chunks` runs first and
unchanged, the checker returns only `sufficient` or `partial`, and it fails
open on any error. Phase 4's caps bound the displacement blast radius; the A/B
on changed-context rows measures it.

The PR5c negative result stands as evidence against the append packaging, not
against the facet checker — the two were never separated in that A/B.
Non-graduation stays logged; `crag-experimental` is the substrate Phase 5
modifies, not a parallel design.

## Design

Two-pass selection, one corrective round maximum:

```text
pass 1 (unchanged serving path):
  fusion pool -> rerank -> edge/parent/sibling expansion -> dedup -> adaptive select
facet check (Haiku, cached, fail-open) on the pass-1 adaptive-selected context
if verdict == partial:
  per missing facet (<= corrective_max_facets): targeted hybrid retrieval,
    top corrective_facet_reserve_n per facet in hybrid fused (RRF) order only
  union = pass-1 pre-rerank fused pool + facet candidates, dedup by chunk ID
pass 2: rerank the union ONCE against the original question
  -> same expansion -> dedup -> adaptive select (Phase 4 v2 caps 7/11/11/2400)
generate over the pass-2 context
```

Design decisions:

1. **Checker input is the pass-1 adaptive-selected context** (what generation
   sees), deviating from PR5's `pre_expansion` choice. PR5's rationale
   ("missing = genuinely absent, not un-expanded") no longer holds: the union
   restores the full pre-rerank pool, so anything packaging dropped can be
   re-surfaced by the global rerank. Owned consequence: `partial` now means
   "missing from the generation-facing context," which conflates retrieval
   misses with packaging drops. This is acceptable because pass 2 unions the
   pre-rerank pool; the CP1 audit classifies the two cases (below).
2. **Union base is the pass-1 pre-rerank fused pool** (the canonical pool per
   schema 1.1) — the roadmap's "pre-selection candidate pool."
3. **Per-facet retrieval does not rerank.** Per-facet candidates are taken in
   hybrid fused (RRF) order only, top `corrective_facet_reserve_n` per facet.
   The sole rerank invocation in the corrective path is pass 2 against the
   original question.
4. **Pass 2 reuses the serving selection code path verbatim** over the union —
   no bespoke merge, no round-robin, no margin filter. `round_robin_merge` and
   `_relevant_to_question` are not used by the new mode; they remain with the
   legacy append mode until it is retired.
5. **Union dedup is two-stage:** before pass-2 reranking the union is deduped
by duplicate `chunk_id` only, in first-seen order (the pass-1 pool instance
wins a collision). The verbatim serving tail then applies full
`dedup_results` semantics after reranking/expansion: represented merged chunks
and fuzzy same-provision similarity, while sibling-expanded leaves are exempt.
This deliberately permits near-duplicates to consume reranker slots in CP3;
if evidence ties target displacement to that effect, full pre-rerank dedup is a
new arm rather than a silent implementation change.
6. **Blast radius:** corrective candidates enter before the global rerank and
   adaptive packaging, so Phase 4's caps bound the final context regardless of
   how many facet candidates are retrieved. Rows judged `sufficient` skip
   pass 2 entirely and are identical to control by construction.

## Knobs and plumbing

| Knob | Candidate value | Control-normalized value | Notes |
|---|---|---|---|
| `evidence_gate` | `crag` | `min_chunks` | Existing field. |
| `corrective_retrieval_enabled` | `true` | `false` | Existing field. |
| `corrective_mode` | `global_rerank` | `append` (legacy normalization) | New field; legacy bundles normalize to `append`, the PR5 behavior. |
| `evidence_judge_model` | `claude-haiku-4-5` | control bundle's recorded value (settings-derived; inert while `evidence_gate=min_chunks`) | Existing PR5 field. |
| `corrective_max_facets` | `3` | `null` (no facet cap existed pre-introduction) | New field. |
| `corrective_facet_reserve_n` | `5` | `null` (knob has no meaning under `append` mode) | New field. Predeclared conservative: if CP3 shows real missing facets just below the cutoff, escalation to 8 is a new sealed arm with its own declared delta, never a silent retune. |

`_SELECTION_KEYS` / `BEHAVIOR_FIELDS` / legacy-defaults entries land in the
same checkpoint that introduces each knob, with a checkpoint test asserting
the comparator reports exactly the declared delta set below (Phase 3/4
lesson, baked in — not a review finding).

**Declared delta set (CP3 comparator gate, set equality):** the matched
comparison of the candidate arm against the `phase4-adaptive-context-v2-minilm`
control must report exactly:

- `evidence_gate: min_chunks→crag`
- `corrective_retrieval_enabled: false→true`
- `corrective_mode: append→global_rerank`
- `evidence_judge_model: <control-recorded>→claude-haiku-4-5`
- `corrective_max_facets: null→3`
- `corrective_facet_reserve_n: null→5`

Any undeclared delta fails the gate. Any missing declared delta fails the
gate.

## Checkpoints (stop for approval after each)

**CP1 — Facet-checker offline audit ✅ PASSED (2026-07-17/18).** Run the
Haiku facet checker over the 131 non-holdout rows' sealed pass-1 contexts,
read from the `phase4-adaptive-context-v2` lineage bundle — no retrieval, no
generation. Phase-2-rewriter discipline: content-addressed cache,
pending-marker, explicit user authorization before the first real call, cost
estimate up front (~131 Haiku calls, one-time, then cache hits).
Deliverables: partial rate; per-row `missing_facets`; hand-check sample of
~15 partial rows plus the `sufficient` verdicts on known evidence-gate-miss
rows (eval_056 pattern); and a mechanical classification of every missing
facet against the sealed canonical pool — **(a) absent from the pass-1
pre-rerank pool** (facet retrieval required) versus **(b) present in the pool
but dropped by selection** (re-selection from the restored pool could recover
it). The (a)/(b) split forecasts how much prospective lift is attributable to
facet retrieval versus re-selection.
**Gate (predeclared):** partial rate within [5%, 35%]; at least half of
sampled missing facets are real gaps; watch-row (`eval_129`, `eval_124`)
verdicts inspected. A bad checker kills the phase here, before any mechanism
is built.

Result: 26/131 (19.85%) rows were `partial`, inside the 5–35% gate; 33/45
hand-checked facets were real gaps. The cache is sealed at
`data/eval_results/facet_audit_cache/v1`; replay is 131/131 hits. The audit
classified 37 facets absent from the pass-1 pool, four dropped by selection,
and four judge-noise cases.

**CP2 — Mechanism and plumbing ✅ DONE (`0f6bf79`).** Implement
union/dedup/global-rerank/re-select as an eval-only arm; knob plumbing per the
table above. Unit tests: empty `missing_facets` skips pass 2 with output
identical to pass 1; union dedup collapses duplicate chunk IDs, represented
merged chunks, and exact normalized text while preserving distinct sibling
leaves; exactly one rerank invocation over the union (per-facet retrieval is
fused-order only); the matched-arm comparator test asserts the exact declared
delta set. No paid calls — the CP1 cache is replayed. Shipping profiles remain
inert.

**CP3 — Retrieval-only sealed run and comparison ✅ PASSED (2026-07-18).** 131 rows,
MiniLM, frozen index, CP1 cache (a cache miss is context drift and stops the
run; no paid-call authorization). Seal as a write-once bundle; publish the
comparison against `phase4-adaptive-context-v2-minilm`.
**Binding gates:** comparator delta set equality; `sufficient` rows identical
on exactly `selected_context_hash`, `context_block_hash`, `source_map_hash`,
`system_prompt_hash`, and `user_prompt_hash`; expected firing IDs exactly equal
the 26 hash-validated CP1 partial rows; and, for each fired row, exact matched
targets from final `selected_results` obey `control ⊆ candidate`. Target
identity is `(source_id, provision_id, unit_label)` when the annotation has a
leaf label, otherwise provision identity (or existing source-only semantics).
The comparator re-hashes the target sidecar and requires both bundles'
`targets_identity` to match; it also validates the CP1 audit rows, summary,
and source-bundle hashes before loading its firing set. Context reads the final
adaptive diagnostic (one direct stage for non-fired rows, two for fired rows;
derived Phase 4 records use their top-level diagnostic): mean ≤1,509.3,
p95 ≤2,649, max ≤3,274, and at most three newly overflowing rows above 2,400.
It reports signed fired-row deltas and resolved/new overflow IDs. CP3 requires
a clean committed worktree, with the relevant Phase 5 behavior files included
in the recorded code identity.

Result: the sealed `phase5-corrective-global-rerank-minilm-r2` candidate passed
the write-once CP3 comparison against `phase4-adaptive-context-v2-minilm`.
All six and only six declared deltas were observed; the firing set was the 26
hash-validated CP1 partial rows; all 105 sufficient rows matched on the five
generation-facing identity hashes; and final selected target preservation had
zero losses. Final rendered context was mean 1,379.96, p95 2,372, max 2,696,
with no newly overflowing rows. The comparison reported five selected-context
hash changes, four fired rows with changed selected IDs, and one displaced
baseline chunk (eval_050) without annotated-target loss. Watch rows eval_129,
eval_124, and eval_058 retained prompt identity; eval_058 fired without a final
selection change. No generation, scoring, holdout access, or CP4 activity ran.
**Small-N regime:** the fired-row count is the regime. If fewer than 10 rows
fire, category slices are direction-only; gates apply to the named
provision/target checks, not slice means.

**CP4 — Matched generation A/B: NOT RUN.** The plan's stop-after-CP3 option was
taken (2026-07-18). The changed-prompt cohort from CP3 is four rows, so CP4's
quality means would have been direction-only under the predeclared small-N rule
regardless of outcome. The paired-harness precondition (declared-delta
validation through sealed retrieval bundles, generation/scoring identity,
changed-subset scoring enforcement, generation-equivalence gate, `error`-field
persistence, set-based abstention/leak gates with rejection tests) is banked as
backlog for a future paired experiment with a larger changed cohort.

**CP5 — Graduation decision: SHELVED (2026-07-18, user decision on CP3
evidence).** The mechanism is operationally sound — every CP3 binding gate
passed, zero target losses, tiny blast radius — but the yield is 4 of 26 fired
rows with a changed generation prompt (one added chunk each), an
exposure-weighted ceiling of ≈4/131 rows against one paid Haiku checker call on
*every* query. CP1's classification already showed the dominant failure mode is
facets absent from the retrieval pool (37 absent-from-pool vs 4
dropped-by-selection), which reranking cannot recover. The arm remains
registered and off (`corrective-global-rerank-experimental`); no ADR, no
serving change. Re-test condition: pool-side recall materially improves
(serving reranker upgrade, corpus growth) so that `partial` verdicts mean
"mis-ranked" rather than "unretrievable." The holdout stays sealed — it was
read once for Phase 4's Stage B; any further holdout access requires a separate
predeclared plan and explicit user approval.

## Explicitly retired / out of scope

- Additive append packaging (`_relevant_to_question` margin filter, post-gate
  append, `corrective_max_added`) — never rerun.
- Re-checking the facet verdict after the corrective round (one round,
  bounded cost).
- Web retrieval, escalate-on-partial arms, and heuristic gating of the checker
  call — out of scope for Phase 5.

# MiniLM vs Qwen3 reranker serving A/B (predeclared 2026-07-18, approved)

Last live "rerun: yes" item from Historical Experiments Worth Rerunning.
Retrieval-only, zero paid calls, holdout sealed. Two fresh sealed 131-row
`original_only` captures under current serving defaults (sibling expansion and
adaptive context on, corrective off), differing only in `reranker_backend`:
`minilm` (serving baseline) vs `qwen3` (local replacement candidate). Tags
`reranker-ab-minilm` / `reranker-ab-qwen3`; comparison `reranker-ab-comparison`
with declared diff `reranker_backend=["minilm","qwen3"]`.

Prior evidence (disclosed): the sealed 2026-07-15 `phase1-gate-*` bundles
(pre-ADR-026/027 stack) show Paraphrase selected survival 0.7319 → 0.8986,
overall 0.8529 → 0.9302, Paraphrase leaf survival 0.7143 → 0.5714 (watch), and
sealed-trace p95 rerank/retrieval MiniLM 0.85 s / 1.59 s vs Qwen3
13.29 s / 14.18 s. Bars below were set with this read in hand, before any
fresh capture exists.

## Predeclared gates (all machine-enforced by the analysis script over
hash-validated sealed bundles; missing timing data fails, never defaults)

1. **Binding integrity:** `pre_rerank_pool_changed = 0/131`; identical
   dataset/target/corpus/index identities; both captures
   `--require-clean-worktree`, strictly sequential processes.
2. **Primary quality:** Paraphrase (n=25) selected-stage target survival,
   Qwen3 − MiniLM ≥ **+0.08** absolute. Ambiguous (n=6) direction-only
   (small-N, n<10) but must not lose more than one row.
3. **No-regression:** Factual (n=70) ≥ baseline − 0.02; Synthesis (n=18)
   ≥ baseline − 0.05; overall selected survival ≥ baseline. OOS (n=12,
   targetless): changed selected contexts reported, informational.
4. **Watch (non-gating):** leaf survival per category, rank-of-target shifts,
   changed-context count.
5. **Operational:** per-row rerank stage latency p95 ≤ **3,000 ms**;
   end-to-end retrieval p95 ≤ **5,000 ms** (full-precision sealed-trace
   values). Model-swap micro-benchmark (3 cycles): cold-load to first rerank
   ≤ **20 s**; rerank latency immediately after a `gemma4:e4b` generation
   ≤ **2×** steady-state p50; byte-stable = identical IEEE-754 score-vector
   serialization for a fixed query/pool across two repeats, all scores
   finite, identical ranking.
6. **Conclusion binding:** context/prompt hashes and target metrics only;
   `selected_context_hash` jitter (CP-A2.c) decides nothing.

## Verdict matrix

- Quality + operational pass → recommend a **local MPS serving flip only**
  (user decision, ADR). This experiment cannot recommend a Fargate/container
  flip: Qwen3-Reranker on CPU is documented at roughly 4–5 minutes per query
  (`app/retriever/reranker.py`), and production is pinned to MiniLM by
  ADR-021. A production flip needs a target-runtime benchmark or a
  GPU-serving architecture.
- Quality pass, operational fail → keep MiniLM serving; record Qwen3 as the
  offline/eval reranker option; note it satisfies the "reranker upgrade" half
  of Phase 5's re-test trigger.
- Quality fail → keep MiniLM everywhere; close the rerun item as answered
  under the current stack.

## Build scope

Comparator: add `_RERANKER_KEYS` (`reranker_backend`, `reranker_model`,
`qwen3_reranker_model`, `bedrock_rerank_model`; top-level shared_values, same
handling as `_EVIDENCE_KEYS`) to declared-delta normalization, observed-delta
detection, and declared-field removal. Tests: one positive acceptance
(declared `reranker_backend` pair publishes) plus rejections — undeclared
reranker drift, wrong endpoints, declared-but-unobserved (both arms same), and
declared backend not masking undeclared `qwen3_reranker_model` drift.
Analysis and swap-benchmark scripts stay in scratch; captures pin
`RAGLAB_PROFILE=local` explicitly alongside the backend override.

## Secondary (historical, non-gating)

Same comparison over the 2026-07-15 `phase1-gate-minilm` / `phase1-gate-qwen3`
bundles: validates the old stack's consistency only, not the current
sibling/adaptive stack.
