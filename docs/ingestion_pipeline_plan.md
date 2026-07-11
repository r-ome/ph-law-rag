# Ingestion Pipeline Plan — Semi-Automated Legal Source Ingestion

Status: **proposed** (not started). Owner: Jerome. Source of truth for the ingestion refactor;
`docs/project_plan.md` remains SoT for the overall architecture.

## Governing principle

> **Automation proposes. Validation constrains. Human review promotes.**

No component in this plan may mutate `sources/ph_law_sources.yaml`. The canonical manifest is
changed only by an explicit human `promote` action against a validated proposal. This is a legal
corpus: a wrong `repeals` edge or a mis-dated effectivity clause silently corrupts retrieval truth
for every downstream query. The cost of a false positive is high, so the pipeline is biased toward
`unknown` / `pending_review` over confident-but-wrong.

## Why this refactor is needed (devlog summary)

Today new sources and their classifications are hand-authored into `ph_law_sources.yaml` with
Codex/Claude Code assistance. That works at 23 sources but has three failure modes as the corpus
grows:

1. **Unaudited trust.** An LLM (or a human in a hurry) writes an `amends`/`repeals` edge with no
   recorded evidence span. The manifest already encodes forward edges that drive provision-level
   supersession (`provision_status.yaml`, `provision_supersession.yaml`) — a bad edge there hides
   operative law or surfaces dead law. There is currently no artifact proving *why* an edge exists.
2. **No reproducibility.** The URL, retrieval timestamp, and content hash that a classification was
   based on are not captured at authoring time. If a source page changes, we can't tell whether our
   metadata still describes the document we read.
3. **Manual bottleneck + inconsistency.** Every addition is bespoke prose reasoning. `doc_type` is a
   free-text string, so classification drifts (`republic_act` vs `ra` vs `statute`).

The fix is a staging layer: a pipeline that discovers, acquires, classifies, extracts relationships
with evidence, validates deterministically, and writes a **reviewable proposal file** — never the
canonical manifest. Humans review a diff, not a blank page. The existing manifest schema is
**locked** (forward-only edges, in-force status, `extra="forbid"`; see memory `manifest-schema`), so
all schema work here is **additive and non-breaking**.

---

## 1. Package structure

New package `app/ingestion/discovery/` and `app/sourcing/` (staging/review is manifest-adjacent, not
raw ingestion). Reuse existing `app/ingestion/{fetcher,parser,hashing,normalizer,storage}.py`.

```
app/
  sourcing/                      # NEW — proposal lifecycle (no business logic in adapters)
    __init__.py
    schema.py                    # ProposalConfig, RelationshipEdge, ReviewMetadata, enums
    analyzer.py                  # orchestrates: acquire → classify → extract → assemble proposal
    classifier.py                # deterministic rules first, LLM fallback for ambiguous
    relationship_extractor.py    # amends/repeals/... + evidence spans + effectivity clause
    metadata_extractor.py        # identifier, title, authority, dates, targets, topics
    validator.py                 # deterministic gate; returns ValidationReport (errors/warnings)
    staging.py                   # write/read sources/staging/*.proposal.yml; diff vs manifest
    promotion.py                 # promote (append to manifest) / reject; review metadata stamping
  ingestion/
    discovery/                   # NEW — later phase; produces candidates only
      __init__.py
      registry.py                # trusted-index adapters (sc_elibrary, lawphil, gazette, ...)
      candidate.py               # Candidate dataclass; dedup vs existing manifest source_ids
  cli/
    source.py                    # NEW — `raglab source ...` sub-typer, wired in main.py

sources/
  staging/                       # NEW — proposal files, git-tracked, reviewable
    <source_id>.proposal.yml
  staging/rejected/              # NEW — rejected proposals with reason (audit trail)
```

Adapters (`app/api`, `frontend/`) hold no logic — a future review UI calls `app/sourcing` functions.

---

## 2. Schema design

### 2.1 Controlled `doc_type` enum (additive hardening)

Today `doc_type: str` is unconstrained. Introduce a `DocType` `Literal` in `app/config.py` and
**validate proposals against it**, but keep `SourceConfig.doc_type` as `str` initially to avoid
breaking the 23 live entries (a follow-up PR tightens it after backfilling). Enum:

```
constitution | code | republic_act | batas_pambansa | presidential_decree |
executive_order | administrative_order | memorandum_circular | supreme_court_decision |
supreme_court_resolution | procedural_rule | rules_of_court_amendment |
department_circular | irr | local_ordinance | secondary_material | unknown
```

`unknown` is a first-class terminal value — a proposal may carry `doc_type: unknown` and still be
staged; it simply cannot auto-fill dependent fields and is flagged for human classification.

### 2.2 Relationship edges with evidence (the core new artifact)

The locked manifest uses bare string lists (`amends: [source_id, ...]`). Proposals use a **richer
edge object** that records evidence; promotion **projects** it down to the manifest's bare-list shape
(so the manifest schema is untouched) while retaining the full edge in the proposal file and in a
sidecar `sources/staging/evidence/<source_id>.edges.yml` for audit.

```yaml
# ProposalConfig.relationships[]
- kind: amends            # controlled enum, see below
  target_source_id: revised_penal_code   # may be null if target not yet in corpus
  target_official_number: "Act No. 3815"
  target_units: ["Article 335"]          # section/article granularity where extractable
  evidence:
    text: "Article 335 of Act No. 3815 ... is hereby repealed."
    char_start: 10432
    char_end: 10498
    locator: "Section 4"                  # where in THIS doc the statement appears
  confidence: 0.0-1.0
  method: deterministic | llm             # how the edge was derived
```

Relationship `kind` enum:
```
amends | repeals | supersedes | revives | implements | renumbers | consolidates |
interprets | declares_unconstitutional | modifies_penalty | changes_jurisdiction | adds_exception
```

**Hard rule:** an edge with no `evidence.text` is never emitted automatically. The extractor drops
it; a human may add it manually during review with `method: human`.

### 2.3 Proposal envelope

```yaml
# sources/staging/<source_id>.proposal.yml
schema_version: 1
proposal_id: <source_id>            # 1:1 with proposed source_id
status: pending_review              # pending_review | validated | promoted | rejected
review:
  reviewed_by: null
  reviewed_at: null
  human_verified_fields: []         # e.g. [status, amends, effectivity_date]
  model_inferred_fields: [...]      # everything the pipeline filled
  overall_confidence: 0.0-1.0
acquisition:
  original_url: "..."
  retrieved_at: 2026-07-09T12:00:00Z
  file_format: html | pdf
  raw_path: data/staging_raw/<source_id>.html
  canonical_text_hash: <sha256 of normalized text>
  source_hash: <sha256 of raw bytes>
  source_priority: 1                # sc_elibrary=1 > lawphil=2 > gazette=3 ... (tie-break)
proposed_entry:                     # mirrors SourceConfig fields exactly (extra=forbid target)
  source_id: ...
  enabled: false                    # promoted entries land disabled; a human enables + syncs
  file_format: ...
  url: ...
  category: ...
  doc_type: ...
  title: ...
  official_number: ...
  approval_date: ...
  effectivity_date: ...
  status: ...
  amends: []                        # projected from relationships at promote time
  repeals: []
  supersedes: []
  implements: []
  source_index: ...
  source_record_id: ...
  structure: auto
relationships: [ ... ]              # rich edges from 2.2
metadata:                           # extractor output not stored on SourceConfig
  authority: "Congress of the Philippines"
  publication_clause: "This Act shall take effect fifteen (15) days after publication ..."
  topics: [...]
  indexing_recommendation: index | skip | review
validation:
  passed: false
  errors: []
  warnings: []
  ran_at: null
```

Proposals promote with `enabled: false` so a human explicitly turns a source on and runs `raglab
sync` — promotion adds a *candidate row*, not live retrieval data.

---

## 3. Component behavior

### 3.1 Discovery (Phase 6 — last)
- `registry.py` holds one adapter per trusted index (`sc_elibrary`, `sc_website`, `lawphil`,
  `official_gazette`, `pco`). Each yields `Candidate{url, title_guess, source_index, discovered_at}`.
- Dedup candidates against existing manifest `source_id`s and against `sources/staging/`.
- Discovery **only creates candidates** (a `sources/staging/candidates.jsonl` queue). It never
  classifies legal truth. Building an analyzer-ready candidate is a separate `stage` step.

### 3.2 Acquisition
- Reuse `app/ingestion/fetcher.py` (never raises) + `parser.py` (HTML via trafilatura→bs4 fallback,
  PDF via pdfplumber) + `hashing.py`. Store into `data/staging_raw/` (separate from prod
  `raw_data_dir`) and record both `source_hash` (raw bytes) and `canonical_text_hash` (normalized
  text) so a re-fetch can detect drift.

### 3.3 Classification
- **Deterministic first** (`classifier.py`): regex/rule table on title + first N chars + host.
  Examples: `^Republic Act No\.` → `republic_act`; host `elibrary.judiciary.gov.ph` + "SUPREME COURT"
  + "DECISION" → `supreme_court_decision`; `Batas Pambansa Blg\.` → `batas_pambansa`; `^Rule \d+` in
  Rules of Court context → `procedural_rule`. Deterministic hit → `method: deterministic`.
- **LLM only on ambiguity** (no deterministic match, or conflicting matches). Prompt returns the
  enum + a short rationale + a self-reported confidence. Sub-threshold confidence → `unknown`.
- **`unknown` is better than wrong** — never guess to fill the field.

### 3.4 Relationship extraction
- Deterministic passes for the high-signal patterns: `is hereby repealed`, `is hereby amended to read
  as follows`, `Section \d+ of (Republic Act|Act) No\. ... is hereby ...`, effectivity clauses
  (`shall take effect ... after publication`). Each match captures the sentence span as evidence.
- LLM pass for the rest, constrained to emit only edges whose evidence span it can quote verbatim
  from the supplied text (post-hoc verify the quote exists in the doc; drop if not — anti-hallucination
  guard). Effectivity/publication clause → `metadata.publication_clause`, not a relationship.
- Target resolution: map `target_official_number` → existing `source_id` where possible; leave
  `target_source_id: null` otherwise (a dangling edge is a warning, not an error).

### 3.5 Metadata extraction
- Identifier/official number, title, authority, approval date, effectivity/publication clause, target
  laws + units, topics, status, indexing recommendation. Dates normalized to ISO `YYYY-MM-DD`.
- Status inference is conservative: default `operative` for a newly enacted law unless the text
  itself or an existing manifest edge says otherwise; never infer `superseded`/`repealed` for the
  *new* doc from its own text.

### 3.6 Validation (`validator.py`) — the gate
Deterministic, no LLM. Returns `ValidationReport{passed, errors[], warnings[]}`. **Errors block
promotion; warnings surface but don't block.**

Errors:
- `source_id` present, slug-shaped, unique vs manifest **and** vs other staged proposals.
- `doc_type` ∈ `DocType` enum (not `unknown` unless explicitly human-acknowledged).
- `category` ∈ `Category`; `status` ∈ `Status`; `source_index` ∈ `SourceIndex`; `file_format` valid.
- Required fields present (`source_id, file_format, url, category, doc_type, title, status,
  source_index`).
- Dates parse as ISO and are sane (`effectivity_date >= approval_date`).
- If `doc_type` is an amending/repealing kind **or** any `amends/repeals/supersedes` edge exists →
  at least one relationship with a resolvable-or-explicitly-null target.
- Every relationship has non-empty `evidence.text` **and** that text is a substring of the acquired
  normalized doc.
- `acquisition.raw_path` exists on disk and its hash matches `canonical_text_hash`.
- `proposed_entry` round-trips through `SourceConfig(**proposed_entry)` (Pydantic `extra="forbid"`).

Warnings: dangling `target_source_id: null`; `doc_type: unknown`; low `overall_confidence`; missing
`effectivity_date`; `indexing_recommendation: review`.

### 3.7 Staging & promotion
- `staging.py`: serialize proposal to `sources/staging/<source_id>.proposal.yml`; produce a
  human-readable diff (`proposed_entry` vs. what a manifest append would look like).
- `promotion.py`:
  - `promote`: re-run validation (must pass), stamp `review` metadata (`reviewed_by`, `reviewed_at`),
    project rich edges → bare manifest lists, **append** `proposed_entry` to `ph_law_sources.yaml`
    (stable key order, `enabled: false`), move proposal to `status: promoted`, retain the file +
    evidence sidecar for audit. Manifest write goes through `SourceFile` load→append→dump so the
    whole file is re-validated.
  - `reject`: move to `sources/staging/rejected/<source_id>.proposal.yml` with a reason; never touch
    the manifest.

---

## 4. CLI design

New `raglab source` sub-typer (`app/cli/source.py`, wired via `app.add_typer(..., name="source")`):

```
raglab source discover [--index sc_elibrary] [--limit N]   # Phase 6: queue candidates only
raglab source analyze <url-or-file>                          # dry-run: print classification + edges + metadata, write nothing
raglab source stage   <url-or-file> [--source-id ID]        # analyze + write sources/staging/<id>.proposal.yml (status: pending_review)
raglab source validate [<proposal-id> | --all]              # run the gate; print errors/warnings; stamp validation block
raglab source diff    <proposal-id>                          # show proposed manifest change
raglab source list    [--status pending_review]             # list staged proposals + their status
raglab source promote <proposal-id> --reviewed-by <name>    # validate → append to manifest (enabled:false) → mark promoted
raglab source reject  <proposal-id> --reason "<why>"        # move to rejected/, never touch manifest
```

`analyze` is side-effect-free (safe to run against anything). `stage` is the first thing that writes.
`promote` is the only thing that touches `ph_law_sources.yaml`, requires `--reviewed-by`, and
re-validates before writing.

---

## 5. Implementation plan (phased, matches recommended roadmap)

| Phase | Scope | Ships |
|------|-------|-------|
| 1 | Schema hardening | `DocType` enum + `ProposalConfig`/`RelationshipEdge`/`ReviewMetadata` in `app/sourcing/schema.py`; `sources/staging/` dir; no CLI yet |
| 2 | Proposal generator | `analyzer.py` + deterministic `classifier.py` + `metadata_extractor.py`; `raglab source analyze` / `stage` (deterministic-only, no LLM) |
| 3 | Relationship extractor | `relationship_extractor.py` (deterministic patterns + effectivity clause) + LLM fallback with quote-verify guard; enrich proposals with evidence-bearing edges |
| 4 | Staging & validation | `staging.py` + `validator.py`; `raglab source validate` / `diff` / `list` |
| 5 | Promotion workflow | `promotion.py`; `raglab source promote` / `reject`; review-metadata stamping; manifest append |
| 6 | Discovery | `ingestion/discovery/`; `raglab source discover`; candidate queue + dedup |

### Minimal first PR (Phase 1 + deterministic Phase 2, no LLM, no manifest writes)

- `app/sourcing/schema.py`: `DocType` enum, `ProposalConfig`, `RelationshipEdge`, `ReviewMetadata`,
  `ValidationReport` (Pydantic, mirrors `SourceConfig` for `proposed_entry`).
- `app/sourcing/analyzer.py` + `classifier.py` (deterministic rules only) + `metadata_extractor.py`
  (regex/heuristic).
- `app/cli/source.py`: `raglab source analyze <url-or-file>` — acquire (reuse `fetcher`/`parser`/
  `hashing`), classify deterministically, extract basic metadata, **print** the assembled proposal
  as YAML. Writes nothing.
- Tests: golden classification cases (one per obvious `doc_type`), acquisition hash stability, schema
  round-trip.

Rationale for the cut: it proves the acquire→classify→assemble spine end-to-end with zero risk (no
files written, no LLM, no manifest mutation), and is independently useful as an inspection tool.

### Follow-up PRs
- PR2: `source stage` writes proposal files (still deterministic).
- PR3: relationship extractor + evidence spans (deterministic, then LLM fallback behind a flag).
- PR4: `validator.py` + `source validate`/`diff`/`list`.
- PR5: `promotion.py` + `source promote`/`reject`; **the first PR that can write the manifest** —
  gate it hard, require `--reviewed-by`, append-only, re-validate the whole file.
- PR6: discovery adapters + candidate queue.
- PR7 (cleanup): backfill `doc_type` on the 23 live entries, then tighten `SourceConfig.doc_type`
  from `str` → `DocType` (breaking-schema change done deliberately, once).

---

## 6. Risks & tradeoffs

- **Manifest mutation is the danger surface.** Kept behind a single human `promote` command that
  re-validates, appends only, and lands `enabled: false`. No automated path writes the manifest.
  *Tradeoff:* slower than autonomous updating — intentional.
- **LLM hallucinated edges.** Mitigated by evidence-required rule + verbatim-substring verification;
  unverifiable edges are dropped, not staged.
- **Source drift.** `source_hash` + `canonical_text_hash` recorded at acquisition; validation
  re-checks the hash so a stale snapshot is caught.
- **Schema divergence from the locked manifest.** Rich edges live only in proposals/evidence
  sidecars; promotion projects to the bare-list manifest shape. The locked `SourceConfig` is
  untouched until the deliberate PR7 `doc_type` tightening.
- **Deterministic classifier brittleness.** Acceptable: a miss yields `unknown` → human review, not a
  wrong classification. Bias is toward under-claiming.
- **Discovery scope creep / OOS moat.** Discovery must respect the corpus scope fence (memory
  `corpus-scope`: tax/corp/civ-pro/election/etc. are deliberately out). Discovery adapters filter to
  in-scope categories; a candidate outside the moat is dropped with a logged reason, not staged.
- **Duplicate / near-duplicate sources.** Dedup on `source_id` and `canonical_text_hash` at both
  discovery and validation; a hash collision with an existing source is a validation error.
- **Provision-level policy files** (`provision_status.yaml`, `provision_supersession.yaml`) are **out
  of scope** here — they encode retrieval policy, not source metadata, and stay hand-curated. A
  promoted amending law may *motivate* a later manual provision-policy edit, but the pipeline does not
  touch those files. (Possible far-future: proposal notes flag "provision-policy edit likely needed.")

---

## 7. Open questions (resolve before Phase 3/5)

1. LLM backend for ambiguous classification / extraction — reuse the `ModelRouter` seam
   (`cascade`/`local-cascade`) or a dedicated cheap model? Leaning: reuse the router, new task profile.
2. Should `promote` also open a git branch/commit, or just edit the working tree and leave commit to
   the human? Leaning: edit working tree only; human commits (keeps the review in the PR).
3. Candidate queue format — `jsonl` vs a `candidates` table in the SQLite DB. Leaning: `jsonl` first,
   promote to a table if discovery volume grows.

---

## 8. Relationship to the live indexing pipeline

This plan governs **admission into the legal corpus**, not the execution of live retrieval indexing.
It applies the same reliability principles as a production document-ingestion pipeline at an earlier
boundary:

- **Record before work:** a proposal records acquisition time, raw/canonical hashes, extracted
  metadata, and relationship evidence before any live manifest change.
- **One source of truth:** `ph_law_sources.yaml` remains canonical; a staging proposal is an
  untrusted, reviewable candidate. The vector and BM25 indexes remain derived from enabled manifest
  sources.
- **Verify before promotion:** deterministic validation and evidence-substring checks constrain
  automation; ambiguous classification is `unknown`, not an assertion of legal truth.
- **Safe re-runs:** acquisition hashes expose source drift, validation is repeatable, and promotion
  re-validates before making the manifest change.

Promotion is the handoff to the existing sync path: it appends a validated source as
`enabled: false`; a human then explicitly enables it and runs `raglab sync`. The proposal workflow
must not directly index a source or edit provision-level retrieval-policy files.

### Future live-indexing reliability follow-on (out of scope for Phases 1–6)

The source-proposal workflow does not require worker queues or lifecycle state. If the corpus moves
from the current local, serial `raglab sync` model to asynchronous or multi-worker indexing, add a
separate follow-on plan with:

1. Persistent per-document indexing state (`PENDING`, `PARSING`, `EMBEDDING`, `INDEXED`, `FAILED`),
   error details, attempt count, and the embedding-model identifier.
2. Atomic claims plus leases/heartbeats so duplicate delivery cannot index the same document twice
   and stalled work can be reclaimed.
3. Transient-error retries with exponential backoff and a terminal failure/dead-letter policy.
4. A reconciliation job that detects stale claims, missing/partial derived chunks, orphaned vectors,
   and embedding-model drift; it should re-drive conservatively from the database record.
5. Parser provenance suitable for citations (at least page/position where available), explicit empty
   extraction failure handling, and OCR only where corpus scope and operating cost justify it.

These are complementary concerns: this plan protects the correctness and auditability of source
metadata before it becomes live; the follow-on would protect the reliability of indexing after it is
live.
