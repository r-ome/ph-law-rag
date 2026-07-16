# Philippine Law RAG — Project Plan

This is the project reference document for `ph-law-rag`.

- Update this file whenever implementation meaningfully changes.
- Use this plan as the default source of truth for architecture, scope, and priorities during implementation and review.
- If the code intentionally differs from this plan, document the reason in review notes or adjacent docs.

See also: `/Users/jeromeagapay/Documents/Personal/muming/03_Outputs/ph-law-rag-devlog.md`

## Retrieval Strategy Phase 3: Sibling-Aware Expansion (experimental 2026-07-16)

The retrieval pipeline now supports an explicit-only `sibling_aware` strategy.
It keeps the frozen MiniLM baseline selection settings and enables one new
post-rerank structural step: after parent expansion, a surviving structured
leaf may recover adjacent leaves from the same `parent_key`. Eligibility comes
from result metadata; family text and ordering are loaded from SQLite
`chunks.metadata_json` and `chunk_index`, so no migration or reindex is needed.

Sibling identity is `(parent_key, unit_label)`. All size-split chunks in that
identity are admitted atomically. Seeds run in rank order, then distance order,
with preceding before following; admitted siblings render in document order
around their seed. Existing survivors and leaves already admitted through
another seed are not moved, duplicated, or charged twice. The initial global
query limits are radius 1, 3,000 added characters, and 750 estimated tokens.
Explicitly hidden leaves remain suppressed under operative-only retrieval, while
missing operability metadata is fail-open.

The serving pipeline order is now:

```text
rerank -> edge expansion -> operative preference -> parent expansion
       -> sibling expansion -> expanded trace -> consolidated dedup -> selected trace
```

Sibling additions are structural rather than reranked and carry their seed score
only for display, plus `expanded_from_sibling`, `sibling_seed_chunk_id`, and
`sibling_offset` provenance. Consolidated dedup preserves these results as
separate leaf identities. The four sibling knobs participate in Settings,
answer-policy behavior identity, retrieval traces, and sealed-bundle comparison.
The Retrieval Lab may select `sibling_aware` explicitly; no intent mapping,
ordinary query default, or automatic serving activation was added.

Retrieval summaries attribute target recovery at the pre-dedup `expanded`
snapshot and verify that recovered leaves remain in `selected`. A read-only
`raglab eval-sibling-census` command joins an existing non-holdout candidate
trace to local SQLite to count radius-eligible exact-leaf misses. If fewer than
six rows are eligible, the planned 80% recovery threshold is descriptive rather
than binding. The sealed `phase2-original-minilm` census found seven rerank-stage
exact-leaf misses and one radius-1-eligible row (`eval_053`), so Phase 3 is in
that descriptive regime. No holdout, paid model call, generation A/B, ADR, or graduation is
part of this implementation checkpoint; those remain gated by the retrieval
experiment documented in `docs/retrieval_strategy_review.md`.

The retrieval-only A/B entry point is
`raglab eval-retrieve --strategy sibling_aware`; without `--strategy`, capture
continues to use the resolved profile default.

### Phase 3 retrieval-only gate result (2026-07-16)

The 131-row non-holdout MiniLM arm sealed as
`phase3-sibling-aware-minilm` with capture consistency matched. Dataset,
targets, corpus, index, embeddings, reranker, cutoffs, and pre-rerank pools
matched the frozen `phase2-original-minilm` baseline; sibling expansion was the
only selection delta. All 131 pre-rerank pool hashes matched.

Sibling expansion fired on 64 rows and added 167 selected chunks. All 64 changed
contexts were additive: no baseline selected chunk or source, provision, or leaf
target was lost. The only target gain was `eval_053`, where Article 1403(2)(e)
was recovered at both `expanded` and `selected`. Selected exact-leaf coverage
rose from 0.5769 to 0.6154; selected source recall, parent-provision coverage,
and overall target survival were unchanged. The structural census therefore
reports 1/1 eligible recovery, but remains descriptive because N=1.

Mean final-context size rose 9.7% (4,725 to 5,182 characters; 1,184 to 1,298
estimated tokens), while mean retrieval latency rose 1.9% (1,049 ms to 1,069
ms). The sibling stage itself averaged 15.4 ms. Per-row limits held: the largest
addition was 2,977 characters and 746 tokens. Only one of 167 additions was a
target leaf, so a matched generation A/B must assess evidence-gate and answer
quality effects before any serving-default or router graduation. The
retrieval-only gate passes in the predeclared descriptive regime; generation
and serving graduation remain pending.

## Phase 2 Checkpoint 4: CLI-Only Two-Lane Retrieval (implemented 2026-07-15)

The retrieval experiment now has an explicit `original_only` versus
`original_plus_rewrite` runtime arm, exposed only by
`raglab eval-retrieve --legal-query-separation/--original-only`. Public
`run_answer()` does not accept or pass the seam, so serving remains structurally
original-only. Rewrite-enabled capture holds the Checkpoint 3 process lock from
before eval-artifact creation through publication and releases it in `finally`;
holdout rejection happens before cache, artifact, lock, rewriter, or model
access. Resume rejects arm or complete retrieval-identity drift.

After history rewriting, intent classification, and retrieval planning, an
accepted strict legal rewrite is forwarded through every strategy. Decomposition
and subquery packaging are rejected before rewrite/retrieval in this arm. Dense
and sparse retrieval run independently for the original and legal-rewrite lanes
with identical knobs, followed by within-lane RRF and deterministic equal-weight
cross-lane RRF with original-lane tie precedence and `chunk_id` deduplication.
The combined pool is reranked exactly once against the original production query;
existing cutoffs, expansion, selection, evidence, corrective behavior, and
fallback metadata remain unchanged.

Schema 1.1 rows retain lane diagnostics, add provenance only to genuinely
combined results, publish exactly one canonical `fused/combined` pre-rerank pool,
and freeze the rewrite decision plus per-row semantic-input hashes and the sealed
`ordered_legal_query_separation_semantic_input_hash`. Latency and cache status
are excluded from semantic identity. Generation replay and original-only capture
remain isolated from the legal rewriter and Anthropic. The schema identity now
mirrors the exact Checkpoint 3 prompt contract without importing the rewriter;
the Checkpoint 3 parser/cache contract itself is unchanged.

Mocked verification passed: the focused Checkpoint 4 selection reported 24
passing tests, and the full unit suite reported 315 passing tests with the same
eight existing RAGAS import deprecation warnings. No smoke, paid experiment,
live retrieval, generation, external/model call, or holdout access was run. The
mechanism remains an offline CLI experiment and has not graduated to serving.

### Post-Checkpoint-4 hardening follow-ups (2026-07-15)

The four Phase 2 checkpoints remain accepted and closed. Schema 1.1
`eval-retrieve` now rejects resolved subquery packaging for both query-separation
arms after the holdout-first gate and before eval-artifact, lock, rewriter,
retrieval, or model access. Public `run_answer()` is unchanged, so serving may
still use its existing packaging behavior; original-only capture remains
isolated from the legal rewriter and Anthropic. The rewrite-arm pipeline/context
guards remain as defense in depth.

The strict parser now permits an existing identifier rendered as
`Republic Act No. 9262` or `RA No. 9262` without treating `No.` as answer prose.
Standalone suffixes beginning `No, ...` or `Yes, ...` remain rejected, and no
other parser, cache, pending-marker, or locking contract changed. Mocked focused
verification reported 52 passed; the full unit suite reported 321 passed with
the same eight existing RAGAS import deprecation warnings. No smoke, experiment,
external/model call, retrieval, or holdout access ran. Scoped whitespace checks
reported no diagnostics.

#### Five-row Phase 2 smoke result (2026-07-15)

The authorized non-holdout five-row smoke sealed original-only, first-rewrite,
cached-rewrite, and comparison artifacts. The control made no rewrite call; the
first rewrite pass wrote five cache misses with no pending residue; and the
cached pass returned five hits. All five Haiku outputs were rejected as
`invalid_output`, so fallback pool/context hashes matched control for every row,
each row reranked once against the original query, and the comparison recorded
zero pool or context changes. No generation or holdout artifact was created.

This passes the smoke's isolation, cache, fallback-parity, rerank, sealing, and
artifact-scope gates, but zero accepted rewrites means the two-lane behavior has
not received live quality evidence. The 131-row experiment remains unrun pending
review; no cache reset, retry, or contract change is implied.

#### One-call v1 parser diagnostic (2026-07-15)

One authorized standalone Haiku call used a predeclared non-holdout query and
the v1 prompt/request parameters, with transport retries disabled. It bypassed
the pipeline, retrieval, rewrite cache, and sealed artifacts; raw text remained
only in a mode-0600 `/private/tmp` scratch file. The first failed parser branch
was `shape.confidence_not_string`: Haiku emitted numeric JSON confidence rather
than the required `"high" | "low"` string. Production parsing therefore
returned `invalid`. No retry or implementation/contract change followed. This
single response diagnoses one concrete v1 prompt-compliance gap but cannot
identify the failure branch of the five hash-only smoke outputs.

#### v2 prompt/prefill hardening and smoke (2026-07-15)

The rewrite prompt now pins the compact JSON schema, exact field types,
`citations=[]`, and string-only `"high" | "low"` confidence. The final Anthropic
message pre-seeds `{"legal_query":"`; parser input is the pinned prefill plus
the returned continuation. Both the prefill and reconstruction rule are included
in the prompt identity mirrored by schema 1.1 without importing the rewriter in
the original-only arm. The v2 prompt hash is
`0737db82638fa3624b591cfbf006a372dc74e147dcf0594683c7ae3c902ee598`,
so retained v1 fallbacks cannot hit v2 keys. The strict parser, cache/pending/
locking contract, fallback behavior, serving path, and general generation seam
are unchanged; `raw_output_hash` remains the hash of the API continuation.

Mocked verification passed 55 focused tests and 324 full unit tests with the same
eight RAGAS deprecation warnings. The newly tagged five-row control made no
rewrite call; the v2 rewrite wrote five misses with no pending residue; and the
cached repeat returned five hits with no new calls. `eval_001` and `eval_034`
were accepted and changed both pool and selected context. `eval_053`, `eval_058`,
and `eval_124` fell back at `literal_violation` with byte-exact control parity.
Every row had one original-query rerank, all bundles sealed, comparison reported
2/5 pool and 2/5 context changes exactly on accepted rows, and no generation or
holdout artifact was created. The 131-row experiment remains unrun pending a
separate review decision.

#### Predeclared v3 prompt and gate-7 amendment (2026-07-15)

Before any v3 smoke or full result, the next prompt version is limited to one
additional instruction: never emit a statute number, act number, article number,
section number, or case/docket number; describe the doctrine or legal concept in
words. The parser, prefill/reconstruction, cache/locking, serving, and retrieval
contracts remain unchanged, while the prompt identity rotates and retains
isolated v1/v2 records.

Gate 7 now separates mechanism reliability from safe non-activation. Across all
131 durable rewrite decisions, operational fallbacks (`timeout`, `llm_error`,
`invalid_output`, `interrupted_after_request`) must be at most 6, regardless of
cache status. Separately, at least 24 of the 31 pooled Paraphrase/Ambiguous rows
must be accepted, valid, high-confidence rewrites. `literal_violation` and
`low_confidence` are reported safety outcomes rather than operational failures,
but count against the target-slice acceptance floor. OOS safety remains governed
by the existing abstention/context gate. These thresholds are frozen before the
v3 smoke and may not be changed after the 131-row result.

The v3 prompt-only amendment is implemented with final prompt hash
`a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91`;
real v1/v2 cache keys are isolated. Focused tests passed 56 and the full unit
suite passed 325 with the same eight warnings. A fresh five-row control made no
rewrite call, the rewrite pass wrote five v3 misses with no pending residue, and
the cached repeat returned five hits with no new call. The Paraphrase and
Ambiguous smoke rows (`eval_034`, `eval_053`) were both accepted and changed pool
and context. The Factual, OOS, and Synthesis-control rows fell back at
`literal_violation` with byte-exact control parity. Thus v3 produced 2/2 target-
slice smoke activation, zero operational failures, 2/5 pool and context changes,
one original-query rerank per row, sealed bundles, and no generation or holdout
artifact. At that checkpoint, the 131-row experiment remained unrun pending
separate authorization; its subsequently authorized result follows.

#### Full 131-row Phase 2 experiment result (2026-07-15)

The authorized matched regression/dev control and v3 rewrite captures and their
comparator sealed under `phase2-original-minilm`,
`phase2-legal-rewrite-minilm`, and
`phase2-legal-rewrite-minilm-comparison`. They used the eval profile,
Qwen3-Embedding at 1024 dimensions, collection `ph_law_qwen06`, MiniLM,
decomposition/packaging disabled, and prompt identity v3 /
`a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91`.
Before the paid run, the direct Anthropic client was pinned to `max_retries=0`
so one cache miss cannot make hidden SDK transport retries; the focused suite
remained 56 passing tests and the full unit suite 325 with the same eight RAGAS
deprecation warnings.

Both arms contain the same 131 non-holdout rows in the same order. The control
made no rewrite access and left the cache unchanged. The rewrite arm produced
73 accepted rows, 47 `literal_violation` fallbacks, 11 `low_confidence`
fallbacks, and zero operational fallbacks. Its five smoke keys were cache hits
and 126 new keys were `miss_written`, for exactly 126 paid requests; the durable
cache ended at v1=5, v2=5, v3=131 with no pending marker. All fallback pools and
contexts are byte-exact with control, accepted rows contain both retrieval lanes
and the combined pool, every row has one original-query rerank, and the largest
rerank input was 60 against the bound of 80. No generation or holdout artifact
was created.

The frozen gates do not graduate the mechanism. Gates 1, 2, 7, and 13 fail:
pooled Paraphrase/Ambiguous provision Hit@8 remains 25/31 rather than improving;
pooled leaf Hit@8 falls 6/11 to 5/11 and leaf MRR `.3212→.2909`; target-slice
activation is 21/31 rather than 24/31 despite zero operational failures; and
manual review finds 19 broadened, altered, or invented legal renderings. Gates
3–6 and 8–12 pass. Factual primary Hit@8 is unchanged at 60/70, Synthesis
improves 16/18 to 17/18 through `eval_050`, OOS hard abstention remains 0/12 and
mean selected count rises 1.5625%, retrieval p95 rises only 185.54 ms, and all
schema/provenance/corpus/index identities match. Of 73 hash-changed contexts,
54 retain byte-identical selected chunk text/order and 19 change content;
manual effects are 3 helpful, 10 harmful, and 60 neutral. The complete metrics,
identity hashes, gate evidence, and 73-row review are in
`docs/retrieval_strategy_review.md`.

Legal-query separation therefore remains an offline, non-graduated experiment.
Serving stays original-only, and no generation replay, rollback implementation,
or further prompt version is implied.

## Phase 2 Checkpoint 3: Strict Legal Rewriter and Paid-Call Cache (implemented 2026-07-15)

The legal-query rewrite experiment now has standalone infrastructure without any
production retrieval integration. `app.retriever.legal_query_rewriter` enforces
the versioned raw-JSON contract, exact original-query and delimiter preservation,
empty citations, high-confidence activation, bounded single-line output, no new
legal numeric identifiers, and answer-prose/alternative rejection. Every invalid,
low-confidence, timeout, or API result produces an original-only fallback.

Rewrite requests use a lazy direct Anthropic seam and a versioned file cache under
`data/eval_results/legal_rewrite_cache/v1/`. A nonblocking process-wide `flock`
can cover the complete future rewrite-enabled capture, while per-key `O_EXCL`
pending markers, atomic replacement, and file/directory `fsync` prevent duplicate
paid calls across interruption and resume. Accepted and fallback decisions are
cached, cached records are validated before use, and pending or malformed-final
residue becomes a durable no-call fallback. Raw model output is never persisted;
only its SHA-256 hash is recorded.

The three rewrite settings are infrastructure defaults only. No enable flag,
serving-path import, two-lane retrieval, generation change, database/API/frontend
change, holdout access, or live/paid model execution is part of this checkpoint.
They are classified only in `INFRA_FIELDS`, not `BEHAVIOR_FIELDS`, so named
profiles cannot pull them into answer-policy resolution.
Checkpoint 4 now supplies the CLI-only arm wiring and holds the capture lock
before eval artifact creation; serving remains original-only.

## Phase 2 Checkpoint 2: Frozen Retrieval Schema and Comparator (implemented 2026-07-15)

New retrieval-only bundles use frozen-context schema 1.1. Each original-only row
retains its dense/sparse/original-fused lane diagnostics and adds one ordered
`fused/combined` snapshot with `pool_role=pre_rerank_pool` immediately before
reranking. Only that canonical snapshot feeds the aggregate fused metrics,
candidate/stage counts, and score-free pre-rerank pool hash; lane metrics remain
available separately by query variant. Validation dispatches pool semantics by
schema minor, accepts only 1.0 and 1.1, and preserves sealed 1.0 validation and
generation replay.

Schema 1.1 retrieval provenance separates common retrieval identity
(`shared_values`/`shared_hash`) from the complete versioned query-separation
contract and its `full_hash`. The current capture arm remains `original_only`;
the identity records the approved future rewrite contract without enabling a
rewriter or two-lane retrieval. Resume compares the complete adapted identity,
while schema 1.0 metadata adapts its former retrieval hash as the shared identity
with an implicit original-only arm.

`raglab eval-retrieval-compare BASELINE_TAG CANDIDATE_TAG --tag REPORT_TAG`
validates two sealed non-holdout bundles before creating output, enforces matched
dataset/target/corpus/index/embedding/reranker/cutoff/selection/evidence identity,
requires original-only versus original-plus-rewrite arms, and atomically publishes
a dated per-row pool/context-change report. The comparator imports no retrieval,
generation, embedding, reranker, Anthropic, or Haiku implementation. This
checkpoint establishes experiment plumbing only; legal rewriting, two-lane
retrieval, experiment execution, and graduation remain pending.

## Phase 1: Reproducible Retrieval Harness (implemented 2026-07-14)

Retrieval preparation is internally separable from generation while the public
`answer()` contract remains unchanged. `app.pipeline.runner.prepare_answer_state`
executes the serving preparation order and `app.pipeline.frozen_generation` is a
generation-only seam used by both normal answers and replay. Retrieval-only
bundles freeze selected results, candidate snapshots, evidence/corrective state,
model routing, and prompt identities under
`data/eval_results/runs/YYYY-MM-DD/<tag>/`; replay reads only a sealed bundle and
does not query retrieval services.

`raglab eval-retrieve` accepts only regression/dev rows and `raglab eval-generate`
creates a separate normal eval bundle. Both use append-and-fsync JSONL rows and
atomic publication. Reranker/embedding release hooks are best-effort; process
separation remains the hard memory boundary. Legacy `raglab eval` and artifact
readers remain available. The holdout remains sealed and is not captured or
replayed by Phase 1 commands.

Retrieval bundles contain `frozen_contexts.jsonl`, `retrieval_trace.jsonl`,
`retrieval_summary.json`, `retrieval_state.json`, and `meta.json`. Schema 1.0
records have canonical per-row hashes; the sealed metadata records ordered row,
pre-rerank-pool, and selected-context hashes plus dataset, target, resolved
retrieval/generation configuration, code, SQLite corpus, BM25, and combined index
identities. Partial JSONL is resumable only when its ordered prefix and provenance
still match; a truncated final fragment is removed, while a valid-but-tampered row
is rejected. Retrieval capture fingerprints SQLite, BM25, and Qdrant both before
and after the row loop; any changed identity or failed end fingerprint leaves the
partial bundle unsealed with a failed state. Replay re-renders context and prompts
from the verbatim frozen metadata, validates all recorded hashes, and passes those
same validated rendered inputs to the generator.

Generation bundles record parity mode and the source frozen-prompt hashes on each
row. Candidate snapshots are stored once at the frozen-record top level and are
rejoined only while deriving the Phase 0-compatible retrieval trace and summary.

MiniLM/Qwen/Bedrock reranker comparisons graduate only when their retrieval
bundles have identical dataset, target, corpus, index, and
`ordered_pre_rerank_pool_hash` identities. Retrieval quality/latency is compared
from the derived Phase 0-compatible summaries; generation comparisons replay the
same sealed retrieval bundle. A best-effort unload warning is recorded but is not
itself a failed experiment because the CLI process boundary is the hard memory
release guarantee.

---

## Goal

Build a serious local-first Python portfolio project that demonstrates:

- LlamaIndex as a RAG orchestration framework
- Hybrid retrieval (dense + sparse BM25 with RRF fusion)
- Cross-encoder reranking for answer quality
- PDF and HTML document ingestion
- Incremental sync with content hashing
- Local LLM generation via Ollama with pluggable backends
- Semantic eval scoring via RAGAS
- Interactive React web frontend (workbench)
- Good software engineering structure

This should feel like a credible production-grade retrieval system over a real legal corpus, not a tutorial demo.

---

## Product Concept

A local RAG assistant over a curated set of Philippine law primary sources — statutes, Supreme Court decisions, and the 1987 Constitution.

It should:

1. Fetch a curated allowlist of law pages and PDFs
2. Normalize and hash content for incremental sync
3. Only reprocess changed or new documents
4. Chunk, embed, and store vectors locally in Qdrant
5. Answer legal questions with a local LLM, grounded in retrieved context
6. Cite source documents and article/section numbers
7. Abstain when evidence is insufficient
8. Support semantic eval scoring via RAGAS
9. Expose a React web UI (workbench) for interactive querying

---

## Scope Constraints

- Python 3.11+ (widely supported, no cutting-edge version requirement)
- Local-first: runs on a normal developer machine with no cloud AI accounts required
- Curated allowlist of URLs and PDFs only — no crawler
- LlamaIndex as the primary orchestration framework
- Ollama as the default LLM and embedding backend
- Keep scope realistic: ~25–40 documents in V1

---

## Recommended Stack

| Concern               | Tool                                   | Reason                                                                                                    |
| --------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| RAG orchestration     | LlamaIndex                             | Purpose-built for document retrieval pipelines; first-class hybrid retrieval, reranking, and eval support |
| LLM                   | Ollama `gemma4:e4b`                    | Graduated local default; model-swappable via config (ADR-025)                                             |
| Embeddings            | Ollama `qwen3-embedding:0.6b`          | Local 1024-dim default with an instruction-prefixed legal query path; Nomic remains the rollback arm (ADR-024) |
| Vector store          | Qdrant (local Docker)                  | Native hybrid search (dense + sparse in one query), metadata filtering, concurrent-safe                   |
| Sparse index          | LlamaIndex BM25Retriever               | Exact-match keyword retrieval; pairs with dense for hybrid                                                |
| Reranker              | Bedrock Rerank (`amazon.rerank-v1:0`) by default; MiniLM serving pin; Qwen3 research arm | Bedrock matches Qwen3 eval quality serverlessly (ADR-021); MiniLM serves (bedrock quota = 2 calls/min); Qwen3 kept for offline research |
| PDF ingestion         | `pdfplumber` via LlamaIndex            | Better table and layout handling than PyPDF2                                                              |
| HTML ingestion        | `trafilatura`                          | Strips navigation boilerplate better than BeautifulSoup                                                   |
| Evals                 | RAGAS                                  | Semantic eval scoring: faithfulness, answer relevance, context precision, context recall                  |
| Frontend              | React + Vite + Tailwind (nginx-served) | Decoupled SPA workbench over the typed REST API; per-phase specs in `docs/frontend/`. (Was Streamlit through M5; see git/devlog for that history.) |
| API                   | FastAPI                                | Thin adapter over shared service modules; feeds the React frontend via OpenAPI-generated types            |
| Config                | pydantic-settings                      | `.env`-driven config with type validation and defaults                                                    |
| Metadata / versioning | SQLite                                 | Zero-setup, ships with Python, sufficient for the workload                                                |
| Dependency management | uv                                     | Fast, lockfile-based                                                                                      |
| Testing               | pytest                                 | Standard                                                                                                  |

---

## Architecture

The system has 5 parts:

### 1. Source Sync Pipeline

- `app.sync_service` owns the `raglab sync` source loop, per-source transaction boundary, indexing handoff, unchanged-content metadata reconcile, status counts, and `sync_runs` insert.
- `app.ingestion` owns source-local ingestion only: fetch, parse, normalize, hash, artifact writes, `documents` upsert, and `document_versions` insert.
- Read allowed sources from `sources/ph_law_sources.yaml`
- Fetch documents (HTTP for HTML, download for PDF)
- Extract text via `trafilatura` (HTML) or `pdfplumber` (PDF)
- Normalize content (whitespace collapse, dedup blank lines)
- Compute SHA-256 hash of normalized text
- Compare against latest stored version in SQLite
- Mark each doc as new, changed, unchanged, or failed
- Persist raw file and normalized text to disk
- Write metadata to SQLite `documents` and `document_versions`

### 2. Indexing Pipeline

- Process only new or changed documents
- Chunk via LlamaIndex `SentenceSplitter` (target ~256 tokens, overlap 32)
- Generate embeddings via Ollama (`qwen3-embedding:0.6b`; 1024 dimensions,
  instruction-prefixed queries and unprefixed document chunks)
- Upsert dense vectors to Qdrant collection
- Build/update BM25 index (LlamaIndex `BM25Retriever`, persisted to disk)
- Delete stale vectors for changed documents before re-indexing
- Write chunk metadata to SQLite `chunks` table

### 3. Query Pipeline

- Embed user query via Ollama
- Run dense retrieval from Qdrant (top-k = 30 candidates by default)
- Run BM25 sparse retrieval (top-k = 10 candidates)
- Merge results via Reciprocal Rank Fusion (RRF)
- Re-score merged candidates with the configured reranker (`bedrock` by default; `minilm` serving pin; `qwen3` research arm)
- Apply `max_distance` filter; apply `min_chunks_for_answer` gate
- Build numbered context prompt with source citations
- Generate answer via Ollama LLM
- Return answer + citation list

### 4. Eval Pipeline

- Load eval questions from `data/eval_dataset.jsonl`
- Load canonical non-holdout retrieval targets from
  `data/eval_retrieval_targets.jsonl`; the hash-locked question dataset remains
  unchanged and the release holdout has no target sidecar.
- Run each question through the full ask pipeline
- Capture opt-in dense, sparse, fused, fully scored rerank, expanded, selected,
  and corrective candidate snapshots without changing serving defaults or
  selected context.
- Score results via RAGAS metrics: faithfulness, answer relevance, context precision, context recall
- Save eval artifacts under `data/eval_results/`: new runs use
  `runs/YYYY-MM-DD/<tag>/` with `run.jsonl`, `meta.json`, `summary.json`,
  `scored.json`, `retrieval_trace.jsonl`, and `retrieval_summary.json`; legacy
  flat files remain readable through the artifact resolver. Candidate rows are
  appended per eval row with a completion sentinel so truncated rows are
  excluded from aggregate retrieval metrics.
- Maintain `manifest.jsonl` and `latest.json` pointers for listing and
  comparing runs without opening every artifact bundle
- Print category-level RAGAS, retrieval, and complete abstention counts. Holdout
  release runs persist aggregate operational retrieval counts and latency only;
  they never write target-quality or category retrieval metrics.

### 5. Interface Layer

- **React web UI** (`frontend/`) — a decoupled SPA "workbench" (chat, corpus browser, dashboard, retrieval lab, evals) consuming the typed REST API; built and nginx-served in Docker. Per-phase specs in `docs/frontend/`.
- **FastAPI** — the typed REST surface the frontend generates against (`/query/ask`, `/documents`, `/conversations`, `/stats`, `/traces`, `/evals`, `/config`, `/health`, …), every route with a Pydantic `response_model`.
- The frontend holds no business logic; FastAPI routes are thin adapters over shared service modules. Runtime probes live in `app.runtime.health`, not in API adapters.

---

## Key Design Principles

### LlamaIndex as Orchestration, Not Lock-in

Use LlamaIndex abstractions for the retrieval and generation pipeline (`VectorStoreIndex`, `BM25Retriever`, `RetrieverQueryEngine`, `NodePostprocessor`, `ResponseSynthesizer`). Keep ingestion logic (fetching, hashing, versioning) outside LlamaIndex — it's just Python. This means swapping components (Qdrant → another vector store, Ollama → OpenAI) is a config change, not a rewrite.

### Hybrid Retrieval From Day One

Dense retrieval alone is insufficient for legal text. Philippine law is full of exact citations — Republic Act numbers, article references, G.R. numbers, section identifiers. BM25 handles these; dense handles semantic intent. Both are needed. RRF fusion is the merge strategy.

### Incremental Sync

Keep the hash-based incremental sync pattern from the original project. It's the right design. Hash normalized text (not raw), compare against SQLite, skip unchanged documents. This makes re-runs cheap and the system safe to run on a schedule.

### Grounded Generation with Abstention

The LLM must only answer from provided context. Abstention is enforced by two mechanisms:

1. Hard gate: if fewer than `min_chunks_for_answer` chunks survive the distance filter, skip generation and return an explicit "insufficient evidence" response.
2. Prompt instruction: the system prompt explicitly instructs the LLM to say it doesn't know when evidence is thin.

### PDF-First Corpus

Philippine law primary sources are predominantly PDFs. PDF ingestion is not optional. `pdfplumber` via LlamaIndex handles layout-aware extraction better than PyPDF2 for multi-column and table-heavy legal documents.

---

## Repository Structure

```
ph-law-rag/
├── README.md
├── pyproject.toml
├── .env.example
├── .python-version          # 3.11
├── sources/
│   └── ph_law_sources.yaml  # curated URL/PDF allowlist
├── app/
│   ├── config.py            # pydantic-settings config
│   ├── db.py                # SQLite bootstrap + migrations
│   ├── sync_service.py      # sync orchestration, transactions, indexing handoff
│   ├── source_metadata.py   # source → chunk metadata mapping
│   ├── runtime/
│   │   └── health.py        # Qdrant/Ollama HTTP probes shared by CLI/API/reindex
│   ├── ingestion/
│   │   ├── fetcher.py       # httpx downloader → FetchResult
│   │   ├── parser.py        # html/pdf extraction helpers
│   │   ├── normalizer.py    # whitespace cleanup
│   │   ├── storage.py       # hash compare, disk write, SQLite write
│   │   └── sync.py          # ingest one source; no indexing or sync_runs ownership
│   ├── indexing/
│   │   ├── chunker.py       # LlamaIndex SentenceSplitter wrapper
│   │   ├── embedder.py      # Ollama embedding client
│   │   ├── vector_store.py  # Qdrant wrapper (upsert, delete, query)
│   │   ├── bm25_store.py    # BM25Retriever build/persist/load
│   │   └── index_service.py # orchestrator: chunk → embed → upsert
│   ├── retriever/
│   │   ├── dense_retriever.py    # Qdrant top-k dense retrieval
│   │   ├── sparse_retriever.py   # BM25 top-k retrieval
│   │   ├── hybrid_retriever.py   # RRF fusion of dense + sparse
│   │   ├── reranker.py           # cross-encoder rescoring
│   │   ├── llm_client.py         # Ollama HTTP client
│   │   ├── prompts.py            # system + grounding prompt templates
│   │   ├── answer_service.py     # full ask pipeline orchestrator
│   │   └── context_builder.py    # numbered prompt + source list
│   ├── evals/
│   │   ├── artifacts.py          # eval artifact paths, legacy fallback, manifest
│   │   ├── runner.py             # runs questions through ask pipeline
│   │   ├── ragas_scorer.py       # RAGAS metric computation
│   │   └── report.py             # aggregates + prints category report
│   └── api/
│       └── main.py               # FastAPI routes
├── frontend/                     # React + Vite web UI (nginx-served in Docker)
├── data/
│   ├── eval_dataset.jsonl        # tracked; eval questions + expected answers
│   ├── raw/                      # gitignored; downloaded HTML/PDF files
│   ├── normalized/               # gitignored; cleaned text
│   ├── qdrant/                   # gitignored; Qdrant local storage
│   ├── bm25/                     # gitignored; BM25 index files
│   ├── sqlite/                   # gitignored; raglab.db
│   ├── eval_results/             # gitignored; eval run outputs
│   └── logs/                     # gitignored; app logs and JSONL traces
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── docs/
    ├── architecture.md
    ├── tradeoffs.md
    └── local_setup.md
```

---

## Milestones

### Milestone 1: Scaffold and Local Runtime

Goal: project boots cleanly, all entry points work.

Build:

- Repo structure and `pyproject.toml`
- `config.py` with pydantic-settings
- `db.py` with SQLite bootstrap and migration table
- Typer CLI stub with `init`, `sync`, `ask`, `eval`, `healthcheck`, `show-config` commands
- FastAPI app with `/health` route
- React web UI (`frontend/`) scaffolded as a separate program (per `docs/frontend/` specs)
- `raglab init` creates data directories and bootstraps DB

Definition of done:

- CLI runs without error
- FastAPI starts
- React web UI builds and serves (`npm run dev`, or nginx in Docker)
- Config loads from `.env`
- DB initializes cleanly

---

### Milestone 2: Document Sync and Normalization

Goal: sync fetches, normalizes, and versions documents.

Build:

- `sources/ph_law_sources.yaml` with ~25–40 curated sources
- `fetcher.py` — httpx downloader with timeout, user-agent, basic retry
- `pdf_parser.py` — pdfplumber extraction with fallback for scanned pages
- `html_parser.py` — trafilatura extraction with BeautifulSoup fallback
- `normalizer.py` — whitespace collapse, dedup blank lines
- Content hashing (SHA-256 of normalized text)
- `storage.py` — hash comparison, disk write, SQLite insert
- `sync_service.py` — orchestrator with per-source status reporting and `sync_runs`; `ingestion/sync.py` ingests one source
- SQLite `documents`, `document_versions`, `sync_runs` tables

Definition of done:

- `raglab sync` fetches all enabled sources
- Changed vs. unchanged is tracked and reported
- Re-running sync on unchanged corpus skips re-fetch versioning AND re-embedding

Metadata-only reconcile (added 2026-06-23): when content is unchanged but a manifest
field changed, the unchanged path still reconciles the derived stores instead of a blind
skip — because some manifest fields (notably `status`) are retrieval filters or are baked
into chunk text. Two tiers:

- Tier A (`status, url, tags, category, doc_type` — not baked): in-place Qdrant
  `set_payload` + chunk `metadata_json` merge + `chunk_parents.url` + BM25 rebuild, no
  re-embed. Reported `[META]`.
- Tier B (`title, official_number` baked into chunk text; `structure` changes boundaries;
  or zero existing chunks): re-chunk + re-embed under the existing `version_id`, no new
  `document_versions` row. Reported `[REINDEX]`.

Failures (e.g. Qdrant down) are counted `failed` with no commit — never a silent stale
skip. `sync_runs` has no columns for these; metadata-only reconciles are folded into
`unchanged_count` (granular `refreshed`/`reindexed_meta` counts live in the run return
dict). Known gap: disabling/removing a source leaves its indexed chunks orphaned
(`load_allowed_sources` skips disabled sources) — needs a separate all-source reconcile.

---

### Milestone 3: Chunking, Embeddings, and Indexing

Goal: changed documents are chunked, embedded, and stored in Qdrant and BM25.

Build:

- `chunker.py` — LlamaIndex `SentenceSplitter` with configurable size and overlap
- `embedder.py` — backend-selecting embedding client; local default is
  `qwen3-embedding:0.6b`, cloud default is Bedrock Titan v2
- `vector_store.py` — Qdrant wrapper: collection init, upsert, delete-by-doc, dense query
- `bm25_store.py` — LlamaIndex `BM25Retriever` with build, persist, and load functions
- `index_service.py` — delete stale vectors, chunk → embed → upsert, update SQLite `chunks`
- Qdrant running locally via Docker

Definition of done:

- `raglab sync` triggers indexing for new/changed documents
- Qdrant holds dense vectors; BM25 index is persisted to disk
- Re-running on unchanged docs skips indexing entirely

---

### Milestone 4: Hybrid Retrieval and Generation

Goal: `raglab ask` returns grounded answers with citations.

Build:

- `dense_retriever.py` — Qdrant top-k dense retrieval with distance filter
- `sparse_retriever.py` — BM25 top-k retrieval
- `hybrid_retriever.py` — RRF fusion of dense and sparse result lists
- `reranker.py` — cross-encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`) with configurable top-n passthrough
- `context_builder.py` — numbered context block with source title, URL, and article/section metadata
- `prompts.py` — grounded system prompt; instructs LLM to cite by reference number and abstain when evidence is thin
- `llm_client.py` — Ollama HTTP client with structured error handling
- `answer_service.py` — full ask pipeline: retrieve → rerank → build context → check abstention gate → generate → package response
- Debug mode: exposes retrieved chunks, distances, rerank scores, prompt length
- Observability: every real `answer()` call persists a local JSONL retrieval trace
  when `trace_logging_enabled=True`, independent of whether debug data is returned
  to the caller. Internal synthetic calls opt out with `trace=False`.
- Eval-only candidate capture is an internal `run_answer()` option. It forces a
  collector even when debug and operational trace logging are disabled, while
  the public `answer()` wrapper and normal serving behavior remain unchanged.

Definition of done:

- `raglab ask "..."` returns a grounded answer with numbered citations
- Out-of-scope questions trigger the abstention response
- Debug mode shows the full retrieval trace in the response; config-gated JSONL
  traces are written for serving and eval calls without exposing debug data.

---

### Milestone 5: Web UI and FastAPI Wiring

Goal: interactive UI works; API is usable.

> The web UI is now the **React workbench** (`frontend/`), built out as its own
> phased program — see *Future Feature: React + Vite Frontend* below and the
> execution specs in `docs/frontend/`. (M5 originally shipped a Streamlit chat
> UI; it was retired once the React chat + corpus workflows reached parity.
> git history and the devlog hold that lineage.)

Build:

- `frontend/` — React SPA consuming the typed REST API: chat with inline
  citations, corpus browser, dashboard, retrieval lab, evals.
- `app/api/main.py` — FastAPI routes, each with a Pydantic `response_model`:
  - `GET /health`
  - `POST /query/ask` — calls `answer_service`
  - `GET /documents` — lists all documents from SQLite
  - `POST /documents/sync` — triggers sync (background task)
- The frontend generates its API types from `/openapi.json`; both it and any
  programmatic client hit the same shared service modules.

Definition of done:

- The React app runs and returns answers in a browser
- FastAPI `/query/ask` returns the same response programmatically
- No business logic lives in either adapter

---

### Milestone 6: Evals

Goal: eval pipeline produces meaningful semantic scores.

Build:

- `data/eval_dataset.jsonl` — 40–60 questions across:
  - Factual lookup (specific article, section, or RA number)
  - Paraphrase (same meaning, different wording)
  - Synthesis (requires combining multiple sources)
  - Ambiguous questions (may or may not be answerable from corpus)
  - Out-of-scope questions (should trigger abstention)
- `runner.py` — feeds questions through `answer_service`, saves results to JSONL
- `artifacts.py` — owns run tags, bundled artifact paths, legacy flat-file
  fallback, `manifest.jsonl`, and `latest.json`
- `ragas_scorer.py` — computes RAGAS metrics per question:
  - **Faithfulness** — is the answer grounded in the retrieved context?
  - **Answer relevance** — does the answer address the question?
  - **Context precision** — are the retrieved chunks actually relevant?
  - **Context recall** — does the retrieved context cover the expected answer?
- `report.py` — aggregates scores by question category, prints summary table
- CLI `raglab eval` runs the full cycle

Definition of done:

- `raglab eval` produces per-question RAGAS scores and a category summary
- Results are saved as bundled artifacts for manual review and indexed in
  `manifest.jsonl`

---

### Milestone 7: Polish and GitHub Readiness

Build:

- `README.md` with setup instructions, demo commands, example output
- `docs/architecture.md` — system design, data flow, package breakdown
- `docs/tradeoffs.md` — design decisions and reasoning
- `docs/local_setup.md` — detailed Qdrant Docker setup, Ollama model pull, first run
- Tests (unit for normalizer, chunker, hash logic; integration for sync and ask pipeline)
- `.env.example` with all configurable values documented
- `docker-compose.yaml` for the full stack: **Qdrant + FastAPI + React web (nginx)** (3 services, one container each — not one container running all three). Ollama is **not** containerized.

#### Full-stack docker-compose design

Three services run in containers; Ollama runs natively on the host so it keeps Apple Silicon GPU access via Metal (containers run in a Linux VM with no Metal/GPU passthrough — a containerized Ollama would be CPU-only and slow). Qdrant/FastAPI/web are CPU+RAM only and do not use the GPU.

Services:

- `qdrant` — image `qdrant/qdrant`, ports `6333:6333` / `6334:6334`, volume for `/qdrant/storage` so vectors persist across restarts.
- `api` — FastAPI (uvicorn), port `8000:8000`, depends_on `qdrant`.
- `web` — React SPA built and served by nginx (`frontend/Dockerfile`), port `8080:80`, depends_on `api`. nginx reverse-proxies `/api` → `api:8000` (see `frontend/nginx.conf`), so the browser only talks to `web`. For frontend iteration, run `npm run dev` on the host (Vite :5173) instead of rebuilding.

Networking rules (Compose puts services on a shared network where the **service name is the hostname**):

- `web` (nginx) → `api` at `http://api:8000` (not `localhost`).
- `api` → `qdrant` at `http://qdrant:6333` (not `localhost`).
- `api` → host-native Ollama at `http://host.docker.internal:11434`. Add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `api` service so this resolves on Colima and Docker Desktop alike.

Host prerequisites (document in README):

- Ollama installed natively, models pulled (`ollama pull qwen3-embedding:0.6b`,
  `ollama pull gemma4:e4b`).
- Ollama must listen on all interfaces so containers can reach it: `OLLAMA_HOST=0.0.0.0:11434 ollama serve` (default `127.0.0.1` binding rejects container traffic).
- Works on Colima (`colima start --cpu 4 --memory 8`) without Docker Desktop.

#### Config overrides when running under docker-compose

These are existing `Settings` fields (`app/config.py`); override via environment in the compose file's `api` service, not by editing defaults. Local (non-Docker) runs keep the `localhost` defaults.

| Config field      | Local default            | docker-compose value (set on `api` service) |
| ----------------- | ------------------------ | ------------------------------------------- |
| `qdrant_url`      | `http://localhost:6333`  | `http://qdrant:6333`                        |
| `ollama_base_url` | `http://localhost:11434` | `http://host.docker.internal:11434`         |

The `web` service needs no API-URL env var — nginx has the `api:8000` upstream baked into `frontend/nginx.conf` and proxies `/api` server-side. Document the `api`-service overrides in `.env.example` with a comment noting the Docker vs. local distinction.

Definition of done:

- Repo is presentation-ready
- A reviewer can clone, follow README, and have a working system in under 15 minutes
- `docker compose up` starts Qdrant + FastAPI + the React web UI; with host Ollama running, the web UI answers an end-to-end query at `http://localhost:8080`

---

#### Cloud deployment (AWS)

The local compose above is for development. The production topology is different — **no local Qdrant, no Ollama** —
and is documented separately in [`docs/aws_deployment_diagram.md`](aws_deployment_diagram.md). Summary:

- **Embeddings** → AWS Bedrock Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`, 1024-dim, `us-east-1`);
  selected by `embedding_backend=bedrock`.
- **Generation** → first-party Anthropic API (Claude Haiku).
- **Vectors** → Qdrant Cloud (collection `ph_law-titan1024`); SQLite + BM25 baked into the image as seed artifacts.
- **Runtime** → two images on Fargate behind an ALB: the FastAPI `api` (Python, from ECR) and the `web` container
  (React SPA + nginx, built by CDK `from_asset("frontend")`). The ALB fronts `web` only; nginx reverse-proxies `/api`
  to the internal API over Service Connect. Secrets via Secrets Manager / task role, never baked.
- **Serving reranker** → `reranker_backend=minilm`; evals/host default to Bedrock Rerank (ADR-021), which matches
  Qwen3 quality but is quota-capped at 2 calls/min — not servable for interactive traffic.
- **Local cloud-smoke** → `docker compose -f docker-compose.cloud.yaml up --build` with
  `RAGLAB_ENV_FILE=.env.cloud-gate` and AWS creds mounted.

Staged rollout (Phase 1 cloud seams → Phase 2 zero-Ollama gate → Phase 3 Dockerfile + ECR → Phase 4 CDK) is tracked in
`docs/aws_deployment_diagram.md`.

## Step-by-Step Implementation Order

1. Scaffold folders and packages
2. Create `pyproject.toml` (Python 3.11+, all deps)
3. Create `config.py`
4. Create `db.py` with migrations
5. Create CLI and FastAPI stubs
6. Scaffold the React web UI (`frontend/`, its own phased program — see `docs/frontend/`)
7. Build source YAML allowlist
8. Implement fetcher
9. Implement `pdf_parser.py` and `html_parser.py`
10. Implement normalizer and storage
11. Implement sync orchestrator
12. Stand up Qdrant via Docker
13. Implement chunker (LlamaIndex SentenceSplitter)
14. Implement embedder (Ollama)
15. Implement vector_store (Qdrant)
16. Implement bm25_store
17. Implement index_service
18. Implement dense_retriever and sparse_retriever
19. Implement hybrid_retriever (RRF)
20. Implement reranker
21. Implement context_builder and prompts
22. Implement llm_client and answer_service
23. Wire CLI `ask` command end-to-end
24. Build the React web UI (`frontend/`, per `docs/frontend/` specs)
25. Wire FastAPI routes
26. Build eval dataset
27. Implement RAGAS scorer and eval runner
28. Write tests and docs
29. Add `conversations` and `conversation_turns` migrations to `db.py`
30. Implement `app/conversation/session.py` and `query_rewriter.py`
31. Update `answer_service.py` for session-aware pipeline
32. Update CLI `raglab ask` with `--session` option
33. Update FastAPI `/query/ask` for threaded sessions
34. Build multi-turn chat state into the React web UI (conversation threading)

---

## Config System

All config lives in `app/config.py`, loaded from `.env` via pydantic-settings.

```python
class Settings(BaseSettings):
    # Paths
    db_path: str = "data/sqlite/raglab.db"
    raw_data_dir: str = "data/raw"
    normalized_data_dir: str = "data/normalized"
    qdrant_path: str = "data/qdrant"
    bm25_path: str = "data/bm25"
    source_config_path: str = "sources/ph_law_sources.yaml"
    eval_dataset_path: str = "data/eval_dataset.jsonl"

    # Models
    embedding_backend: Literal["ollama", "bedrock"] = "ollama"
    embedding_model: str | None = None  # backend default when unset
    embedding_dim: int | None = None     # backend/model default when unset
    embedding_query_instruction: str | None = None  # backend default when unset
    llm_model: str = "gemma4:e4b"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_backend: Literal["minilm", "qwen3", "bedrock"] = "bedrock"
    qwen3_reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    bedrock_rerank_model: str = "amazon.rerank-v1:0"
    bedrock_rerank_region: str = "us-west-2"   # rerank models are not in us-east-1

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # Chunking
    chunk_size: int = 256
    chunk_overlap: int = 32

    # Retrieval
    dense_top_k: int = 30
    sparse_top_k: int = 10
    rerank_top_n: int = 8
    max_distance: float = 0.5
    min_chunks_for_answer: int = 1
    consolidated_dedup_enabled: bool = True

    # Qdrant
    qdrant_collection: str = "ph_law_qwen06"

    # Misc
    request_timeout: int = 30
    debug: bool = False
    log_dir: str = "data/logs"
    log_level: str = "INFO"
    log_to_file: bool = True
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5
    trace_logging_enabled: bool = True
    trace_max_text_preview: int = 200
```

---

## Data Model

### `documents`

One row per logical source document.

| Field         | Type    | Notes                                               |
| ------------- | ------- | --------------------------------------------------- |
| `doc_id`      | TEXT PK | Derived from source_id + URL hash                   |
| `source_id`   | TEXT    | From YAML `source_id` field                         |
| `title`       | TEXT    | From YAML or extracted from document                |
| `url`         | TEXT    | Source URL                                          |
| `doc_type`    | TEXT    | `statute`, `sc_decision`, `constitution`, `other`   |
| `file_format` | TEXT    | `html`, `pdf`                                       |
| `category`    | TEXT    | e.g., `civil_law`, `criminal_law`, `constitutional` |
| `tags_json`   | TEXT    | JSON array of tags                                  |
| `enabled`     | INTEGER | 1 = active source                                   |
| `created_at`  | TEXT    | ISO8601                                             |
| `updated_at`  | TEXT    | ISO8601                                             |

### `document_versions`

One row per fetched version of a document.

| Field                   | Type    | Notes                                        |
| ----------------------- | ------- | -------------------------------------------- |
| `version_id`            | TEXT PK | UUID                                         |
| `doc_id`                | TEXT FK | → documents                                  |
| `fetched_at`            | TEXT    | ISO8601                                      |
| `http_status`           | INTEGER | HTTP response code                           |
| `content_hash`          | TEXT    | SHA-256 of normalized text                   |
| `content_length`        | INTEGER | Char count of normalized text                |
| `raw_path`              | TEXT    | Path under `data/raw/`                       |
| `normalized_path`       | TEXT    | Path under `data/normalized/`                |
| `extraction_method`     | TEXT    | `trafilatura`, `pdfplumber`, `beautifulsoup` |
| `changed_from_previous` | INTEGER | 1 if content changed                         |

### `chunks`

One row per chunk version.

| Field            | Type    | Notes                                             |
| ---------------- | ------- | ------------------------------------------------- |
| `chunk_id`       | TEXT PK | UUID                                              |
| `doc_id`         | TEXT FK | → documents                                       |
| `version_id`     | TEXT FK | → document_versions                               |
| `chunk_index`    | INTEGER | Position in document                              |
| `text`           | TEXT    | Chunk content                                     |
| `char_count`     | INTEGER |                                                   |
| `token_estimate` | INTEGER | Rough estimate                                    |
| `qdrant_id`      | TEXT    | Qdrant point ID for deletion                      |
| `metadata_json`  | TEXT    | title, url, doc_type, category, tags, chunk_index |
| `created_at`     | TEXT    | ISO8601                                           |

### `sync_runs`

| Field             | Type    |
| ----------------- | ------- |
| `sync_run_id`     | TEXT PK |
| `started_at`      | TEXT    |
| `completed_at`    | TEXT    |
| `status`          | TEXT    |
| `scanned_count`   | INTEGER |
| `changed_count`   | INTEGER |
| `unchanged_count` | INTEGER |
| `failed_count`    | INTEGER |

### `conversations`

| Field        | Type    | Notes          |
| ------------ | ------- | -------------- |
| `session_id` | TEXT PK | UUID           |
| `created_at` | TEXT    | ISO8601        |
| `title`      | TEXT    | Optional label |

### `conversation_turns`

| Field                   | Type    | Notes                                           |
| ----------------------- | ------- | ----------------------------------------------- |
| `turn_id`               | TEXT PK | UUID                                            |
| `session_id`            | TEXT FK | → conversations                                 |
| `turn_index`            | INTEGER | Position in session (0-based)                   |
| `question`              | TEXT    | Original user question                          |
| `rewritten_question`    | TEXT    | Rewritten standalone query (may equal question) |
| `answer`                | TEXT    | Generated answer                                |
| `retrieved_chunks_json` | TEXT    | JSON array of chunk IDs used                    |
| `created_at`            | TEXT    | ISO8601                                         |

### `schema_migrations`

| Field         | Type       |
| ------------- | ---------- |
| `version`     | INTEGER PK |
| `applied_at`  | TEXT       |
| `description` | TEXT       |

---

## Source Config

`sources/ph_law_sources.yaml` structure:

```yaml
sources:
  - source_id: constitution_1987
    title: "1987 Constitution of the Philippines"
    url: "https://www.officialgazette.gov.ph/constitutions/1987-constitution/"
    doc_type: constitution
    file_format: html
    category: constitutional
    tags: [constitution, fundamental_law]
    enabled: true

  - source_id: civil_code
    title: "Civil Code of the Philippines (RA 386)"
    url: "https://www.lawphil.net/statutes/repacts/ra1950/ra_386_1950.html"
    doc_type: statute
    file_format: html
    category: civil_law
    tags: [civil_code, obligations, contracts, property]
    enabled: true
```

### Starter Corpus Themes

Prioritize sources that represent the core of Philippine law:

- **Constitutional law** — 1987 Constitution
- **Civil law** — Civil Code (RA 386), Family Code (EO 209)
- **Criminal law** — Revised Penal Code (Act 3815)
- **Special laws** — Anti-VAWC (RA 9262), Cybercrime Prevention Act (RA 10175), Data Privacy Act (RA 10173), IP Code (RA 8293)
- **Landmark SC decisions** — 5–10 decisions from SC E-Library covering major constitutional or civil law issues

Target: ~30 sources in V1.

### Corpus Expansion Roadmap (criminal law)

Corpus growth is phased and controlled — not "all criminal law forever." Each phase is a deliberate, bounded expansion with its own eval/OOS-moat review, because adding sources turns previously out-of-scope eval questions in-scope and can silently shift abstention/leak numbers.

**Principle — manifest truth vs. retrieval behavior.** `amends`/`repeals`/`status` are manifest facts. They do *not* automatically change what the retriever prefers:
- Edge expansion uses only `amends` and `implements` (`app/retriever/edges.py`); `repeals` is recorded metadata only.
- Amendment laws with `amends` enter amendment-aware chunking: quoted inserted Article/Section markers are structural only in those documents, and inserted provisions keep amendment `source_id` provenance while their `provision_id` is emitted in the target law namespace. Use `amends_namespace` when a document amends multiple sources and the inserted text targets one namespace.
- "Prefer the newer wording" happens solely through `sources/provision_supersession.yaml` (provision-level reorder, off by default via `prefer_operative`), and only when both base and operative chunks already retrieve. Supersession rules match exact `provision_id` values plus `source_id`; same-number amendments intentionally share the target provision_id with the base source.
- Retrieval suppression is driven by a single field, `operability_action` (`hide`/`show`/`flag`), computed at index time and filtered on by both retrieval arms (`vector_store.operative_filter`, `sparse_retriever`). Its default is derived from the **document** `status` (so `repealed`/`superseded` whole documents are hidden), and it can be overridden **per provision** via `sources/provision_status.yaml` for whole-provision repeal/reclassification (e.g. RPC Art 335 → RA 8353 Art 266-A) or for curated partial amendments using exact `unit_labels` scoped by `source_id` (e.g. RA 10640 rewrote §21 chapeau/items (1)-(3) while items (4)-(8) remain operative). Leaf-scoped overrides stamp hidden leaves with `operability_action: hide` and surviving siblings with `parent_has_hidden_leaves: 1`, which makes parent expansion keep fragments rather than swap in parent text containing hidden leaves. Document `status` is never overwritten by an override; provision state lives in `provision_status`/`operability_action` beside it. Edits to `provision_status.yaml` are applied at index time and require `raglab reindex`. Same-id amendment collisions must never become unscoped overrides; a `source_id`-scoped whole-provision hide is the correct mechanism when the base provision is wholly dead because the scoping prevents the hide from touching the amendment's same-id inserted text. Add a `provision_supersession.yaml` pair only when stale text remains retrievable, usually partial restatements; fully hidden families intentionally omit inert pairs. Anti-hazing remains the collision-scoping precedent, and its older pair is grandfathered and harmless.
- Metadata convention: `build_source_metadata()` omits falsy routing keys such as empty `amends` and unset `amends_namespace`. This prevents a one-time mass reindex for non-amendments while still making amendment manifest edits Tier B drift that re-chunks unchanged text.
- Per-provision amendment timelines are built read-only from `chunks.metadata_json` by `app/indexing/amendment_timeline.py`. The primary identity is each chunk's `provision_id`; path-less inserted provisions resolve into a path-scoped base provision only on exact or unique target-namespace matches. Ambiguous path-less insertions, same-date insertion collisions, and missing approval dates are diagnostics, not guessed ordering. `raglab timeline <fragment>` inspects matching timelines and `raglab timeline --summary` reports corpus totals plus diagnostic counts. Timeline data feeds mechanical consolidation, but the timeline builder itself remains read-only.
- Mechanical consolidation (`app/indexing/consolidation.py`) intentionally changes retrieval behavior at **reindex** time only. Bucketed provisions must have a base entry, exactly one non-partial insertion, length ratio 0.7-1.5, no matching `provision_status.yaml` override, and a dry-run preflight whose recomputed partial flag agrees with stored chunk metadata. Consolidation splices the amendment's full restatement into the base text before both child chunking and parent extraction, appends inline `[as amended by ...]` provenance, stamps consolidated payload metadata on base chunks, and hides the duplicate amendment insertion chunks with `operability_action: hide`. Full reindex builds one plan from the pre-reindex snapshot and enforces coherence after indexing; doc-scoped reindex auto-expands to paired base/amendment docs so one side is never refreshed alone. Sync does not trigger consolidation and writes no new `document_versions` rows for it.

So the rule when adding an amended penal law: add the amendment too; set `amends` and, for multi-target amendments, `amends_namespace`; for a *wholly* dead base provision add a bare `provision_status.yaml` override unless the replacement collides on `provision_id`, in which case scope the override with `source_id`; for a partial amendment only add a `source_id` + exact `unit_labels` override when the replacement text is indexed and operative; add a `provision_supersession` reorder rule only for high-risk both-retrieved pairs (trace-gated).

**Phase A — Core criminal law + common SPLs (current expansion).**
- RPC (Act 3815) + key amendments (RA 10951)
- Sentencing modifiers: ISL (Act 4103), Probation (PD 968 + RA 10707), Juvenile Justice (RA 9344 + RA 10630)
- Common SPLs: drugs (RA 9165 + RA 10640), VAWC (RA 9262), cybercrime (RA 10175), BP 22, child abuse (RA 7610), trafficking (RA 9208 + RA 10364 + RA 11862), anti-graft (RA 3019 + RA 10910), plunder (RA 7080), firearms (RA 10591), carnapping (RA 10883), hazing (RA 8049 + RA 11053), child pornography → OSAEC (RA 9775 `repealed` → RA 11930), Safe Spaces (RA 11313), statutory rape (RA 11648)
- Current-law correctness sources: RA 9346 (death-penalty prohibition — global penalty modifier, standalone + curated supersession rules)
- Deferred within A: RA 7658 / RA 9231 (child-labor amendments — drift toward fenced labor/social-legislation territory)

**Phase B — Public-order / national-security crimes.** Anti-Terrorism Act, rebellion/sedition special laws, Human Security Act predecessors.

**Phase C — Financial / commercial penal laws.** AMLA, banking secrecy, securities-regulation offenses.

**Phase D — Election, tax, customs, environmental, local-government offenses.** Omnibus Election Code offenses, NIRC/Customs penal provisions, environmental penal laws, LGC offenses.

**Phase E — Historical predecessors and repeal chains.** Older repealed predecessors and deeper amendment chains for jurisprudential/temporal-retrieval value.

Phases B–E are explicitly out of the current OOS moat's in-scope set; expanding into them requires re-baselining the eval suite. The current expansion is Phase A only.

---

## Hybrid Retrieval Design

### Dense Retrieval (Qdrant)

- Embed query via `qwen3-embedding:0.6b` (1024 dimensions) using the legal
  retrieval instruction recorded in ADR-024
- Query Qdrant collection with cosine similarity
- Retrieve top-k = 30 candidates with scores and metadata

### Sparse Retrieval (BM25)

- LlamaIndex `BM25Retriever` built from all indexed chunks at sync time
- Persisted to `data/bm25/` as a serialized index
- Retrieve top-k = 10 candidates with BM25 scores

### RRF Fusion

Reciprocal Rank Fusion merges the two ranked lists:

```
RRF_score(doc) = Σ 1 / (k + rank_i(doc))
```

where `k = 60` (standard constant), and `rank_i` is the position in each retriever's result list. Documents appearing in both lists get a boosted combined score.

### Reranking

After RRF fusion, merged candidates are re-scored by the configured selector:

- Input: `(query, chunk_text)` pairs
- Eval/host default (ADR-021): `reranker_backend=bedrock` via the Bedrock Rerank API (`amazon.rerank-v1:0`,
  us-west-2, ~0.8s/query, quota-capped 2 calls/min → paced 31s apart)
- Serving pin: `reranker_backend=minilm` using `cross-encoder/ms-marco-MiniLM-L-6-v2` (compose, cloud compose,
  Fargate — the quota makes bedrock unservable for interactive traffic)
- Research arm: `reranker_backend=qwen3` using `Qwen/Qwen3-Reranker-0.6B` (prior eval default, ADR-016; GPU-only)
- Output: relevance score per pair (bedrock scores are uncalibrated — ordering only)
- Top `rerank_top_n` (default: 8) form the post-rerank/pre-parent-expansion list used by answer gates
- Parent expansion and consolidated dedup then produce the final selected generation/inspection context

### Why This Matters for Legal Text

| Query type                         | Dense handles | BM25 handles    |
| ---------------------------------- | ------------- | --------------- |
| "What are the elements of estafa?" | ✓ (semantic)  | ✗               |
| "Republic Act 10173 section 16"    | ✗             | ✓ (exact match) |
| "G.R. No. 12345"                   | ✗             | ✓ (exact match) |
| "rights of an accused person"      | ✓ (semantic)  | partial         |

---

## Chunking Design

- Use LlamaIndex `SentenceSplitter`
- Target chunk size: ~256 tokens
- Overlap: 32 tokens
- Preserves sentence boundaries
- Chunk metadata attached at index time:
  - `doc_id`, `version_id`, `source_url`, `title`, `doc_type`, `category`, `tags`, `chunk_index`

---

## Generation Design

System prompt (grounding):

```
You are a Philippine law assistant. Answer questions based only on the
provided legal sources. Do not invent statutes, case citations, or legal
interpretations not present in the context. When citing, reference the
source number (e.g., [1], [2]). If the provided context does not contain
sufficient information to answer the question, say: "I don't have enough
information from the available sources to answer this question."
```

Response structure:

- Direct answer
- Inline citations by reference number
- Sources section listing title, URL, article/section where applicable

Abstention gate: if fewer than `min_chunks_for_answer` chunks survive the `max_distance` filter, skip generation and return the insufficient-evidence response directly.

---

## Eval Strategy

### Dataset Design

The tracked eval dataset currently has 70 questions across categories. Keep the out-of-scope slice stable when expanding the corpus so abstention metrics remain comparable across runs.

| Category     | Count | Description                                        |
| ------------ | ----- | -------------------------------------------------- |
| Factual      | 34    | Specific article, section, or RA lookup            |
| Paraphrase   | 8     | Same meaning as a factual query, different wording |
| Synthesis    | 10    | Requires combining context from 2+ sources         |
| Ambiguous    | 6     | May be partially answerable                        |
| Out-of-scope | 12    | Should trigger abstention                          |

### RAGAS Metrics

| Metric            | What it measures                                          |
| ----------------- | --------------------------------------------------------- |
| Faithfulness      | Is the answer supported by the retrieved context?         |
| Answer relevance  | Does the answer actually address the question?            |
| Context precision | Are the top-ranked chunks relevant to the question?       |
| Context recall    | Does the retrieved context cover the ground-truth answer? |

### Eval Dataset Format

```jsonl
{
  "question": "What is the prescriptive period for filing a criminal case for estafa?",
  "ground_truth": "Under Article 90 of the Revised Penal Code, the prescriptive period for estafa depends on the penalty attached to the offense.",
  "expected_sources": [
    "revised_penal_code"
  ],
  "category": "factual"
}
```

---

## Web UI Design (React workbench)

The frontend is a decoupled React + Vite SPA — a "legal RAG workbench," not a
generic admin dashboard. Full design and the phased build are in
*Future Feature: React + Vite Frontend* (below) and the execution specs in
`docs/frontend/`. Surface at a glance:

- **Chat** — questions with inline `[n]` citations resolving to source cards; read-only conversation history/replay.
- **Corpus browser** — filterable document table + detail (metadata, amendment edges, normalized text, chunks).
- **Dashboard / Ingestion / Health** — corpus & index stats, config summary, sync-run history + Run-sync, service status.
- **Retrieval Lab / Observability** — inspect a query's full trace (retrieved → reranked → selected, scores, strategy), browse persisted traces, tail logs.
- **Evaluations** — eval-run list, per-question drill-down, run-to-run metrics diff.

> Retired Streamlit predecessor: a two-tab chat/sources app (`app/ui/home.py`),
> replaced once React reached parity. git history + the devlog hold that lineage.

---

## FastAPI Endpoints

| Method | Path                  | Description                                            |
| ------ | --------------------- | ------------------------------------------------------ |
| GET    | `/health`             | Health check; verifies Qdrant and Ollama are reachable |
| POST   | `/query/ask`          | Ask a question; returns answer + citations             |
| GET    | `/documents`          | List all documents with sync status                    |
| GET    | `/documents/{doc_id}` | Single document metadata                               |
| POST   | `/sync`               | Trigger sync as background task                        |

---

## Local Setup Requirements

- Python 3.11+
- Docker (for Qdrant)
- Ollama installed and running
- uv or pip

### First Run

```bash
# 1. Clone and install
git clone https://github.com/your-username/ph-law-rag.git
cd ph-law-rag
uv sync

# 2. Start Qdrant
docker-compose up -d

# 3. Pull Ollama models
ollama pull gemma4:e4b
ollama pull qwen3-embedding:0.6b

# 4. Configure
cp .env.example .env

# 5. Initialize
raglab init

# 6. Sync corpus
raglab sync

# 7. Ask a question
raglab ask "What are the elements of a valid contract under the Civil Code?"

# 8. Run the React web UI (from frontend/)
cd frontend && npm install --legacy-peer-deps && npm run dev   # http://localhost:5173

# 9. Run evals
raglab eval
```

---

## Tradeoffs and Constraints

**LlamaIndex over LangChain** — LlamaIndex has more opinionated, better-abstracted primitives for document retrieval. For a RAG project (not a general agent), it's the right choice. LangChain would be appropriate if the project later needed complex multi-step tool use or agent loops.

**Qdrant over ChromaDB** — Qdrant supports hybrid search natively (dense + sparse in one query), has a stable concurrent-safe architecture, and runs well in Docker. ChromaDB's PersistentClient has known issues with concurrent access. The tradeoff is requiring Docker for local setup.

**RAGAS over custom eval scoring** — RAGAS provides semantic scoring via LLM-graded metrics, which is significantly more meaningful than keyword matching. The tradeoff is that RAGAS requires a working LLM at eval time (uses Ollama), adding an extra dependency to the eval pipeline.

**Trafilatura over BeautifulSoup** — Trafilatura strips navigation boilerplate, which is the primary quality problem in the original project. The tradeoff is a less familiar library with slightly less control over what gets extracted.

**Local-first with Ollama** — No cloud API keys required. The tradeoff is lower model quality than GPT-4 or Claude. For a portfolio project demonstrating pipeline design, this is the right call. The LLM backend is pluggable via config if cloud inference is desired later.

**Document-level re-indexing** — Same tradeoff as the original: when a document changes, all its vectors are deleted and the entire document is re-chunked and re-embedded. Simpler than chunk-level diffing and fast enough for a small corpus.

---

### Milestone 8: Conversation Context Management

Goal: multi-turn conversations work end-to-end — follow-up questions are resolved against prior context before retrieval.

Build:

**New SQLite tables (new migration in `db.py`):**

- `conversations` — `session_id` (PK), `created_at`, `title` (optional label)
- `conversation_turns` — `turn_id` (PK), `session_id` (FK), `turn_index`, `question`, `rewritten_question`, `answer`, `retrieved_chunks_json`, `created_at`

**New config fields in `app/config.py`:**

```python
max_conversation_turns: int = 5       # history window passed to rewriter
enable_query_rewriting: bool = True   # toggle rewriting off for debugging
```

**New module `app/conversation/`:**

- `session.py` — `create_session(conn) -> str`, `get_history(conn, session_id, limit) -> list[dict]`, `append_turn(conn, session_id, turn_data) -> str`
- `query_rewriter.py` — `rewrite_query(question: str, history: list[dict]) -> str`: calls Ollama LLM with a short prompt that resolves pronouns and ellipsis ("what about that?", "and section 5?") into a self-contained query; returns original question unchanged if `enable_query_rewriting = False` or history is empty

**Changes to existing files:**

- `answer_service.py` — accept optional `session_id: str | None`; if provided, load history, rewrite query, run pipeline on rewritten query, persist turn to `conversation_turns`
- `app/cli/main.py` — `raglab ask` gains `--session TEXT` option; if omitted, creates a new session each invocation (stateless); if provided, loads and continues that session
- `app/api/main.py` — `POST /query/ask` request body gains optional `session_id`; response includes `session_id` so clients can thread turns
- React web UI (`frontend/`) — thread `session_id` through the chat; display full conversation history and replay; "New conversation" resets state (see the Chat phase in `docs/frontend/`)

**Query rewriting prompt (in `prompts.py`):**

```
Given the following conversation history and a follow-up question, rewrite
the follow-up as a standalone question that can be understood without the
history. Do not answer the question — only rewrite it. If the follow-up is
already self-contained, return it unchanged.

History:
{history}

Follow-up: {question}
Standalone question:
```

Definition of done:

- `raglab ask --session abc "what about section 5?"` correctly resolves "section 5" against prior turns in session `abc`
- The React web UI maintains conversation state across turns in the browser
- `POST /query/ask` with `session_id` returns a threaded response
- Sessions with no history bypass rewriting (no unnecessary LLM call)
- `max_conversation_turns` caps how much history is passed to the rewriter

Key constraints:

- Rewriting is a separate LLM call before retrieval — keep it short (use a fast/small model or the same Ollama model with a low token budget)
- Never pass raw history into the retrieval prompt — only the rewritten standalone query goes to the retriever
- History is stored in SQLite, not in-memory — sessions survive process restarts

---

## Future Feature: Exam Mode and Bar Readiness

Exam Mode is a separate product workflow for bar takers and law students. It should not be treated as ordinary legal Q&A. The goal is to simulate bar-style practice, grade user-written answers against private references/rubrics, and return actionable feedback.

### Product Concept

The user selects a subject, year range, difficulty, and number of questions. The app presents bar-style questions under timed conditions. After the user submits an answer, the system produces a practice score plus feedback explaining where the answer earned points, where it lost points, and what legal analysis was missing.

This feature is a strong candidate for a later commercial or hosted version because it is naturally meterable: one submitted answer roughly equals one billable grading event.

### Data Sources

- Supreme Court bar questionnaires are public and can be used as exam prompts where allowed by source terms.
- Supreme Court questionnaires are available for recent bar years, including questionnaires up to 2025.
- Suggested answers, such as UP Law Center suggested answers, may be purchased and used privately as ground truth/reference material.
- The currently identified suggested-answer range is 2012 through 2022. Later questionnaires may exist without corresponding purchased suggested answers yet.
- Do not publish copyrighted suggested-answer text unless the license expressly permits it.
- Public reports may publish aggregate metrics, category scores, and failure analysis without redistributing private answer keys.

This creates two related but distinct datasets:

1. **Question-only dataset** — public bar questions, potentially covering 2012 through 2025, useful for retrieval smoke tests, mock exams, and manual answer review.
2. **Graded eval dataset** — questions with private suggested answers/rubrics, currently expected to cover 2012 through 2022, useful for scoring model answers and user-written exam answers.

Suggested private file organization:

```text
data/evals/bar_exam/
  questions_2012_2025.jsonl                   # public prompts/metadata where allowed
  suggested_answers_2012_2022.private.jsonl   # purchased/private, gitignored
  rubrics_2012_2022.private.jsonl             # derived from suggested answers, gitignored
  eval_items_2012_2022.private.jsonl          # joined question + rubric IDs, gitignored
```

Suggested metadata fields:

```json
{
  "question_id": "bar_2025_pil_prior_restraint_abc_news",
  "year": 2025,
  "subject": "Political and Public International Law",
  "question_number": "I",
  "source": "Supreme Court Bar Questionnaire",
  "has_suggested_answer": false,
  "suggested_answer_source": null,
  "category": "application",
  "skills": ["issue_spotting", "doctrine_application", "synthesis"],
  "expected_sources": ["constitution_1987", "prior_restraint_cases"]
}
```

Suggested question IDs:

```text
bar_2025_pil_prior_restraint_abc_news
bar_2024_civil_oblicon_q03
bar_2022_crim_special_laws_q05
```

### Scoring Model

Use rubric-based grading, not simple semantic similarity. A typical rubric should break points down by:

- issue spotting
- rule accuracy
- application to facts
- conclusion
- citation/source support where appropriate
- clarity and organization

Example rubric shape:

```json
{
  "question_id": "bar_2025_pil_prior_restraint_abc_news",
  "max_score": 10,
  "criteria": [
    {
      "name": "Identifies prior restraint / freedom of press issue",
      "points": 2
    },
    { "name": "States the presumption against prior restraint", "points": 2 },
    {
      "name": "Discusses narrow exceptions / clear and present danger",
      "points": 2
    },
    {
      "name": "Applies doctrine to DOJ prohibition and violence facts",
      "points": 2
    },
    { "name": "Reaches and explains the correct conclusion", "points": 1 },
    { "name": "Clear bar-style structure", "points": 1 }
  ]
}
```

### Feedback Output

The grader should return structured JSON so UI and reports can be built reliably:

```json
{
  "score": 6.5,
  "max_score": 10,
  "issue_spotting": 1.5,
  "rule_accuracy": 2,
  "application": 1.5,
  "conclusion": 1,
  "clarity": 0.5,
  "what_went_well": [],
  "what_cost_points": [],
  "missing_analysis": [],
  "wrong_or_unsupported_points": [],
  "study_recommendations": []
}
```

### Architecture Notes

- Keep Exam Mode separate from the general ask pipeline.
- Runtime grading should usually not need live retrieval because the question, reference answer, and rubric are already known.
- Use RAG only when the feature needs to show source support, explain a doctrine, or debug a grading result.
- Use one cloud judge call per submitted answer where possible.
- Cache grading results by a hash of `question_id + rubric_version + user_answer`.
- Precompute rubrics and reference summaries offline.
- Use local models for low-risk tasks such as study-plan formatting, but prefer a stronger cloud model for final grading if accuracy matters.

### Cost Controls

- Grade only on submit, never while the user is typing.
- Limit answer length per question.
- Cache repeated submissions.
- Keep a free tier limited by number of graded answers.
- Consider paid grading credits or a subscription plan.
- Batch analytics and cohort reports offline.
- Keep infrastructure simple: a single VPS plus SQLite/Postgres and object storage is enough for an MVP.

### Commercial Segments

- Bar takers: roughly 10k-11.5k examinees per recent year; high urgency and clear willingness to prepare.
- Law students: larger surrounding market; useful for subject drills and semester review.
- Practicing lawyers: potentially higher willingness to pay, but the quality and liability bar is much higher.

Initial wedge should likely be bar takers or law students, not practicing lawyers. Exam Mode offers measurable progress and lower legal-risk framing than a production legal-advice assistant.

### Sequencing

Do not build Exam Mode before the retrieval system is stable. First improve the current retrieval metrics, especially:

- multi-hop/synthesis retrieval
- provision-aware indexing
- query decomposition
- neighboring chunk/section expansion
- debug traces showing which query found which chunk

Once the current eval set is stable, start with a small private bar-exam slice:

1. One subject, such as Political Law or Civil Law
2. 30-50 curated questions
3. Private suggested answers and hand-authored rubrics
4. Cloud-judge grading
5. Manual audit of scores and feedback quality

Definition of done for an MVP:

- User can start a timed practice session
- User can submit written answers
- System returns a structured practice score and feedback
- Results are saved per user/session
- Aggregate per-subject and per-skill weakness reports are available
- Private answer keys/rubrics are gitignored and never exposed in public artifacts

---

## Phase 2 Ideas

After V1:

- Metadata filtering in Qdrant queries (filter by doc_type, category, RA number)
- Query routing: classify question as constitutional / civil / criminal and filter corpus
- Chunk-level incremental indexing
- Support for scanned PDF OCR (Tesseract)
- Scheduled sync
- OpenAI / Anthropic / Bedrock LLM backend via LlamaIndex adapters
- LangChain integration for agent-based multi-hop legal research
- Comparative mode: "how does RA 10173 relate to RA 10175 on data privacy?"
- Export answers as formatted legal memos

---

## Ongoing Review Instructions

When reviewing code:

1. Compare implementation against this plan
2. Flag unnecessary complexity — LlamaIndex abstractions should simplify, not obscure
3. Keep business logic out of the React frontend and FastAPI adapters
4. Preserve incremental-sync architecture
5. Preserve local-first design
6. Prefer explicit retrieval traces over silent failures. Debug mode may expose
   traces in responses; local JSONL trace persistence is config-gated and enabled
   separately from response debugging.

If the current implementation differs from this plan, note whether the difference is:

- Acceptable simplification
- Technical debt
- Bug
- Scope creep
- Worthwhile improvement

---

## React + Vite Frontend (Legal RAG Workbench)

The web UI. A React SPA that feels like a **legal RAG workbench**, not a generic SaaS admin dashboard: chat, corpus browsing, citations, retrieval traces, eval quality, source lifecycle, and cost visibility are the differentiators. Built as a phased program (execution specs in `docs/frontend/`), it replaced the original Streamlit UI once chat + corpus workflows reached parity.

Implementation state (Phases 1–5 shipped; 6 deferred):

- Phases 1–5 are built and reviewed: corpus browser, chat + citations + conversations, dashboard/ingestion/health, retrieval lab + observability, and evaluations. Phase 6 (Cost & Usage) is deferred pending token accounting (see `docs/frontend/README.md`).
- FastAPI now exposes the full read/serving surface the workbench needs — documents/chunks, conversations, stats, sync runs, traces, logs, evals, config, retrieval inspect — each with a Pydantic `response_model`.
- The predecessor Streamlit app (`app/ui/home.py`) has been removed; git history + the devlog hold that lineage.
- The underlying data these endpoints expose:
  - SQLite: `documents`, `document_versions`, `chunks`, `sync_runs`, `conversations`, `conversation_turns`, `chunk_parents`.
  - Logs: `data/logs/app.log`.
  - Retrieval traces: `data/logs/traces/*.jsonl`.
  - Eval artifacts: `data/eval_results/manifest.jsonl`, bundled run directories, summaries, scored outputs, and diffs.

Keep this as an adapter swap: business logic stays in Python service modules; React calls thin FastAPI endpoints.

### Stack (decided)

- **Vite + React + TypeScript** (TS non-negotiable — portfolio signal).
- **React Router** — list → detail routing.
- **TanStack Query** — data fetching/caching/loading-states for the GETs.
- **TanStack Table** — the document list is a filter-by-`doc_type`/`category` table.
- **Tailwind v4 + shadcn/ui** — Tailwind via the `@tailwindcss/vite` plugin (CSS-first, no `tailwind.config.js`); Radix primitives copied into the repo; polished tables/inputs fast.
- **Vite dev proxy** `/api → http://localhost:8000` — sidesteps CORS in dev (no FastAPI middleware). Prod build served static by the compose/CDK `web` service (nginx), which reverse-proxies `/api` → `api:8000` the same way (`frontend/nginx.conf`).

shadcn/ui setup notes: needs Tailwind configured **and** path aliases (`@/*`) in both `tsconfig.json` and `vite.config.ts` before `npx shadcn@latest init`. Components copied into `src/components/ui/` on demand; Phase 1 pulls `table button badge input select scroll-area`.

### TypeScript & API typing (decided)

The API boundary is typed end-to-end, not hand-declared:

- **Strict TS everywhere.** On top of the `react-ts` template's `strict: true`, enable `noUncheckedIndexedAccess`, `noImplicitReturns`, and `exactOptionalPropertyTypes`. Applies to all app code **and** the copied-in `src/components/ui/**` — patch shadcn files inline as strictness errors surface (no per-directory relaxation). Note `exactOptionalPropertyTypes` forces explicit `| undefined` on optional props in a few places.
- **Types generated from OpenAPI.** `openapi-typescript` runs against FastAPI's `/openapi.json` and emits `frontend/src/api/schema.ts` (committed). `src/api/client.ts` imports the generated `paths` types; no hand-written request/response models. Workflow: `npm run gen:types` (script → `openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts`) after any route change.
- **Every route needs a Pydantic `response_model`.** This is the gate for useful codegen — routes returning bare dicts emit as untyped `object`. Adding/typing `response_model` is part of each backend phase's work, including retrofitting the existing `GET /documents`, `POST /query/ask`, and health routes.

### API docs & testing (decided)

One OpenAPI schema drives everything downstream — generated TS types, Swagger, and ReDoc all derive from the same Pydantic `response_model`s, so they cannot drift.

- **Swagger UI / ReDoc — already shipped by FastAPI.** Served at `/docs` and `/redoc`; no new dependency. Work is enrichment only: per-route `tags`, `summary`/`description`, and response `examples` so `/docs` reads as real documentation. Keep `/docs` enabled in prod (portfolio surface).
- **Unit/component — Vitest + React Testing Library.** Covers filters, citation rendering, and `client.ts` wrappers. Fast feedback loop; runs under the same strict TS config as app code.
- **E2E — Playwright, hybrid backend.** Default CI lane mocks `/api/*` via route interception against committed fixtures (deterministic, fast, no Qdrant/Ollama/LLM, no token cost). A separate small real-backend smoke lane (corpus browse + one chat) drives the true stack, run manually or nightly to catch contract/pipeline breaks. Fixtures are derived from the generated schema so mocks stay shape-accurate.

### Backend read surface (built)

The read APIs the workbench needs — all shipped across Phases 1–5, each with a Pydantic `response_model` (paths reflect the as-built routes):

- `GET /documents/{doc_id}` — metadata + latest normalized text from `document_versions.normalized_path`.
- `GET /documents/{doc_id}/chunks` — chunk list with metadata and qdrant IDs.
- `GET /stats/overview` — corpus totals, chunk totals, latest sync, recent query counts, basic latency and error metrics from traces/logs.
- `GET /sync/runs` — latest rows from `sync_runs`.
- `GET /traces` and `GET /traces/{trace_id}` — list and inspect JSONL retrieval traces.
- `GET /evals/runs` and `GET /evals/runs/{tag}` — expose `manifest.jsonl`, summary, scored artifacts, and paths.
- `GET /conversations` and `GET /conversations/{session_id}` — stored chat sessions and turns.
- `GET /config` — read-only current settings with secrets redacted.

The file reads and SQLite queries should live in `db.py` or small service modules. FastAPI routes remain thin adapters. Each endpoint above ships with a Pydantic `response_model` so it lands in the OpenAPI schema and flows into the generated TS types.

### Information architecture

Recommended React pages:

- **Chat** — primary user-facing legal assistant with citations, debug toggle, conversation state, and retrieved context inspection.
- **Corpus** — public-readable source/document browser. This can absorb the original `Documents` page: filters by category, doc type, legal status, tags, and source index; detail view with normalized text and source URL.
- **Sources** — curated source config view from `sources/ph_law_sources.yaml`: enabled/disabled, availability, source index, official number, legal status, tags, amendment/supersession relationships.
- **Ingestion** — source sync operations and history: manual sync button, latest sync runs, failed fetches, changed/unchanged counts, version history.
- **Retrieval Lab** — developer/research view for one query: dense results, BM25 results, fused candidates, reranked results, selected context, scores, chunk previews, and stage timings.
- **Evaluations** — eval run index and detail: faithfulness, answer relevancy, context precision, context recall, abstention accuracy, by-category breakdowns, and diff links.
- **Observability** — logs, traces, query latency, retrieval stages, errors, and trace search.
- **Conversations** — browse stored sessions from `conversations` and `conversation_turns`; useful for debugging follow-up rewriting.
- **Health** — Qdrant, Ollama/LLM backend, DB, BM25 files, API status, and current runtime profile.
- **Settings** — read-only config first; later editable local feature flags for reranker backend, top-k values, parent expansion, query rewriting, answerability gate, and self-check.
- **Cost & Usage** — internal cost accounting by model/backend/day/trace label. Do not call this "Pricing" unless it becomes customer-facing. Current traces include prompt length and latency but do not yet provide reliable input/output token accounting; add a usage ledger before treating this as authoritative.
- **Amendment Timeline** — visualize operative/superseded/repealed chains and provision-level relationships. This is a legal-domain differentiator and should be more prominent than generic user-count stats.

Page feedback:

- Keep **Dashboard** as the landing overview: health, corpus size, latest sync, latest eval score, recent queries, error rate, and latency.
- Rename broad **Statistics** to **Analytics** unless real user/account tracking exists.
- Rename **Pricing** to **Cost & Usage** for internal model/provider accounting.
- Keep **Ingestion** focused on source lifecycle; keep **Corpus/Documents** focused on reading and inspecting legal text.

### Folder layout

```
frontend/                      # sibling to app/, own package.json
  src/
    api/client.ts              # fetch wrapper + typed API models
    routes/
      Dashboard.tsx
      Chat.tsx
      CorpusList.tsx           # table + filters
      CorpusDetail.tsx         # metadata header + normalized text pane
      Sources.tsx
      Ingestion.tsx
      RetrievalLab.tsx
      Evaluations.tsx
      Observability.tsx
      Conversations.tsx
      Health.tsx
      Settings.tsx
      CostUsage.tsx
      AmendmentTimeline.tsx
    components/                # shared + components/ui/ (shadcn)
    App.tsx                    # router
    main.tsx
  vite.config.ts               # proxy /api → :8000
  package.json
```

### Phasing

- **Phase 1 (done):** corpus browser — list + filters + detail view + source metadata.
- **Phase 2 (done):** chat + inline `[n]` citations on `/query/ask`, with conversation state + read-only conversation replay.
- **Phase 3 (done):** dashboard + ingestion + health using SQLite sync rows, document/chunk counts, and runtime probes.
- **Phase 4 (done):** retrieval lab + observability using trace JSONL and app logs.
- **Phase 5 (done):** evaluations using `data/eval_results/manifest.jsonl` and bundled summaries/scored outputs.
- **Phase 6 (deferred):** cost and usage — blocked on token accounting (generators discard `resp.usage`; traces carry `prompt_length` chars, not tokens). Needs backend instrumentation first.
- **Retirement (done):** Streamlit was retired on parity — the compose/CDK UI service was repointed from Streamlit to the React nginx build, and `app/ui/` removed.

### Build order

Execution-fidelity specs (one file per phase, with acceptance criteria) live in [`frontend/`](frontend/) — see [`frontend/README.md`](frontend/README.md). Those specs are the handoff artifacts for delegated execution; this section is the summary.

Per-phase loop for every endpoint: **add route with Pydantic `response_model` → curl-verify → `npm run gen:types` → build the frontend slice against generated types.**

**Phase 0 — scaffold (one-time):**

1. `npm create vite@latest frontend -- --template react-ts`; install deps (`react-router-dom @tanstack/react-query @tanstack/react-table`; dev: `openapi-typescript @types/node vitest @testing-library/react @testing-library/jest-dom jsdom @playwright/test`).
2. Tailwind v4: `npm i tailwindcss @tailwindcss/vite`; add plugin to `vite.config.ts`; `@import "tailwindcss";` in `src/index.css`.
3. Path aliases `@/*` in `tsconfig.json` **and** `vite.config.ts` (before shadcn); `npx shadcn@latest init`; `add table button badge input select scroll-area`.
4. `vite.config.ts` proxy `/api → http://localhost:8000`.
5. tsconfig strict extras (`noUncheckedIndexedAccess`, `noImplicitReturns`, `exactOptionalPropertyTypes`).
6. Add Pydantic `response_model` to existing `GET /documents`; `npm run gen:types` → `src/api/schema.ts`; `src/api/client.ts` imports generated `paths` types. Done when `tsc --noEmit` is clean and the shell renders live data through the proxy.

**Phase 1+ (features):**

7. `GET /documents/{doc_id}` + `db.py` reader (with `response_model`) → curl → regen types.
8. CorpusList (TanStack Table + `doc_type`/`category`/status/source filters).
9. CorpusDetail (metadata header + scrollable normalized text pane).
10. Chat page on `/query/ask` with citation rendering and debug trace panel.
11. Dashboard, Ingestion, and Health pages backed by overview/sync/health endpoints.
12. Retrieval Lab and Observability pages backed by trace/log endpoints.
13. Evaluations page backed by eval manifest and summary endpoints.
14. Cost & Usage only after token/provider usage data is captured reliably.

### Open question / tradeoff (deferred)

A React rebuild competes for time with deeper legal-RAG capabilities. Treat it as polish unless the frontend itself becomes the portfolio focus. The strongest UI angle is not generic analytics; it is making retrieval quality, legal source lifecycle, citations, and eval evidence inspectable.
