"""Passage-level eval-diff report.

Reads saved eval artifacts (run_*.jsonl + scored_*.json) — no RAGAS spend — and
classifies every question into one failure class, so per-question failures that
aggregate scores hide become visible. With a baseline tag it diffs the two runs.

The classes map directly to build decisions:
  FRAGMENTATION? heavy -> parent-child / auto-merging retrieval
  DOC_MISS / PROVISION_MISS heavy -> retrieval / corpus / chunk targeting
  CROSS_SOURCE heavy -> operative-law / source-versioning handling
  OOS_LEAK / IN_SCOPE_ABSTAIN heavy -> abstention gate

Honesty rules baked in (see classify): provision-level classes only fire when
the ground truth actually names a provision; FRAGMENTATION carries a "?" because
it is a heuristic; CROSS_SOURCE only fires for an UNASKED competing version. A
high OTHER count means diagnosis coverage is weak (ground truth named no
provision), NOT that those questions are fine.
"""

import re
import json
from pathlib import Path

from app.config import settings
from app.evals import artifacts

# Worst-first; source-level problems rank above provision-level ones.
CLASS_ORDER = ["OOS_LEAK", "IN_SCOPE_ABSTAIN", "DOC_MISS", "CROSS_SOURCE",
               "PROVISION_MISS", "FRAGMENTATION?", "OK", "OTHER"]

# Competing statute versions in the corpus. CROSS_SOURCE fires only when a pair's
# members are both retrieved but NOT both expected (an unasked version leaked in).
COMPETING = [
    {"dangerous_drugs_act", "dangerous_drugs_amendments_2014"},
    {"judiciary_reorganization_act", "judiciary_reorganization_amendments_2021"},
    {"revised_penal_code", "rpc_penalty_amendments_2017"},
    {"revised_penal_code", "anti_rape_law_1997", "statutory_rape_amendments_2022"},
    {"revised_penal_code", "death_penalty_prohibition"},
    {"anti_trafficking", "anti_trafficking_expanded_2013", "anti_trafficking_amendments_2022"},
    {"probation_law", "probation_amendments_2015"},
]

# Ground truth: "Section 13, Article III" | chunk header: "SECTION 13" / "Section 4(c)(4)".
_SEC = re.compile(r"\bsec(?:tion)?\.?\s*(\d+(?:\([a-z0-9]+\))*)", re.I)
_ART = re.compile(r"\bart(?:icle|\.)?\s*([ivxlc]+|\d+)\b", re.I)


def provisions(text: str) -> set[str]:
    """Normalize section/article references to comparable tokens (S13, S4(c)(4), Aiii)."""
    out = {f"S{m}" for m in _SEC.findall(text or "")}
    out |= {f"A{m.lower()}" for m in _ART.findall(text or "")}
    return out


def _chunk_prov(chunk: str) -> set[str]:
    """Provisions a chunk actually *carries* — parsed from its label line only, not
    the body. A body cross-reference (e.g. '...as defined in Article 355...') must
    NOT count the cited section as retrieved. Skips a leading 'Source: <title>'
    attribution line (commit 7da46e9) so the real label on the next line is read."""
    lines = [ln for ln in (chunk or "").split("\n") if ln.strip()]
    if lines and lines[0].lstrip().startswith("Source"):
        lines = lines[1:]
    header = lines[0][:200] if lines else ""
    return provisions(header)


def _only_fragments(need: set[str], got: set[str]) -> bool:
    """A needed parent section is present in context ONLY as enumerated children
    (e.g. need S21, got S21(1)/S21(4) but not S21). Heuristic — subparts are
    sometimes exactly what the question needs, hence the trailing '?' on the class."""
    for p in need:
        if not p.startswith("S") or p in got:
            continue
        if any(g.startswith(p + "(") for g in got):
            return True
    return False


def _cross_source(exp: set[str], got: set[str]) -> bool:
    for pair in COMPETING:
        if len(pair & got) >= 2 and (pair & got) - exp:
            return True
    return False


def classify(rec: dict) -> str:
    exp = set(rec["expected_sources"])
    got_src = set(rec["retrieved_sources"])
    should_abstain = not exp

    # Abstention is decided first — never falls through to retrieval classes.
    if should_abstain:
        return "OOS_LEAK" if not rec["abstained"] else "OK"
    if rec["abstained"]:
        return "IN_SCOPE_ABSTAIN"

    # In-scope, answered: retrieval-level diagnosis.
    if not exp.issubset(got_src):          # partial retrieval of a multi-source answer is a miss
        return "DOC_MISS"
    if _cross_source(exp, got_src):
        return "CROSS_SOURCE"

    need_prov = provisions(rec.get("ground_truth", ""))
    if need_prov:
        got_prov = set().union(*(_chunk_prov(c) for c in rec["contexts"])) if rec["contexts"] else set()
        exact = need_prov & got_prov
        fragmented = _only_fragments(need_prov, got_prov)
        if not exact and not fragmented:
            return "PROVISION_MISS"
        if fragmented:
            return "FRAGMENTATION?"
        return "OK"
    return "OTHER"     # ground truth named no provision — diagnosis can't go deeper


def _load(tag: str) -> dict[str, dict]:
    """Merge a run with scores, preferring stable eval IDs over question text."""
    run_path = artifacts.existing_path(tag, "run", required=True)
    run = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scored_path = artifacts.existing_path(tag, "scored")
    scored = json.loads(scored_path.read_text(encoding="utf-8")) if scored_path else []
    by_id = {s["eval_id"]: s for s in scored if s.get("eval_id")}
    by_q = {s["user_input"]: s for s in scored}
    out = {}
    for r in run:
        key = r.get("eval_id") or r["question"]
        s = by_id.get(r.get("eval_id")) or by_q.get(r["question"], {})
        r["context_recall"] = s.get("context_recall")
        r["faithfulness"] = s.get("faithfulness")
        r["answer_relevancy"] = s.get("answer_relevancy")
        r["context_precision"] = s.get("llm_context_precision_with_reference")
        out[key] = r
    return out


def _is_holdout(tag: str) -> bool:
    return bool((artifacts.load_meta(tag) or {}).get("holdout"))


def _aggregate(rows: dict[str, dict]) -> dict[str, float | int | None]:
    records = list(rows.values())
    out: dict[str, float | int | None] = {
        "n": len(records),
        "abstain_count": sum(bool(record.get("abstained")) for record in records),
    }
    for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        values = [record[key] for record in records if isinstance(record.get(key), (int, float))]
        out[key] = round(sum(values) / len(values), 4) if values else None
    return out


def _trunc(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _md_num(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


def build_diff_report(experiment: str, baseline: str | None = None, out: str | None = None) -> Path:
    exp_runs = _load(experiment)
    base_runs = _load(baseline) if baseline else {}
    holdout = _is_holdout(experiment) or bool(baseline and _is_holdout(baseline))
    if holdout:
        from app.evals.holdout_ledger import log_holdout_aggregate_read

        exp_meta = artifacts.load_meta(experiment) or {}
        base_meta = artifacts.load_meta(baseline) if baseline else {}
        log_holdout_aggregate_read(
            access_type="diff_report",
            tags=[tag for tag in [experiment, baseline] if tag],
            purpose=(exp_meta or {}).get("label") or (base_meta or {}).get("label") or None,
            source="evals.diff_report.build_diff_report",
        )
        lines = [f"# Eval diff — {experiment}" + (f"  (vs {baseline})" if baseline else ""), "",
                 "## Holdout release aggregates", "",
                 "| run | n | abstain count | faithfulness | relevancy | precision | recall |",
                 "|---|--:|--:|--:|--:|--:|--:|"]
        for label, records in [(experiment, exp_runs), *(([(baseline, base_runs)] if baseline else []))]:
            aggregate = _aggregate(records)
            lines.append(
                f"| {label} | {aggregate['n']} | {aggregate['abstain_count']} | "
                f"{_md_num(aggregate['faithfulness'])} | {_md_num(aggregate['answer_relevancy'])} | "
                f"{_md_num(aggregate['context_precision'])} | {_md_num(aggregate['context_recall'])} |"
            )
        out_path = Path(out) if out else Path(settings.eval_results_dir) / "diffs" / f"diff_{experiment}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n")
        return out_path
    base_class = {q: classify(r) for q, r in base_runs.items()}

    rows = []
    for q, r in exp_runs.items():
        cls = classify(r)
        recall = r.get("context_recall")
        b = base_runs.get(q)
        b_recall = b.get("context_recall") if b else None
        d_recall = (recall - b_recall) if (recall is not None and b_recall is not None) else None

        display = cls
        if cls == "IN_SCOPE_ABSTAIN":      # honest split: gate-too-strict vs. justified-by-retrieval
            justified = not set(r["expected_sources"]).issubset(set(r["retrieved_sources"]))
            display = "IN_SCOPE_ABSTAIN (justified)" if justified else "IN_SCOPE_ABSTAIN (gate)"

        rows.append({
            "question": q, "category": r["category"], "class": cls, "display": display,
            "recall": recall, "d_recall": d_recall,
            "exp_src": ",".join(sorted(set(r["expected_sources"]))) or "—",
            "got_prov": ",".join(sorted(set().union(
                *(_chunk_prov(c) for c in r["contexts"])) if r["contexts"] else [])[:4]) or "—",
            "answer": r["answer"],
        })

    # Sort: worse class -> negative Δrecall -> larger movement -> category/question.
    rows.sort(key=lambda x: (
        CLASS_ORDER.index(x["class"]),
        x["d_recall"] if x["d_recall"] is not None else 0,
        -abs(x["d_recall"]) if x["d_recall"] is not None else 0,
        x["category"], x["question"],
    ))

    exp_counts = {c: 0 for c in CLASS_ORDER}
    base_counts = {c: 0 for c in CLASS_ORDER}
    for r in rows:
        exp_counts[r["class"]] += 1
    for c in base_class.values():
        base_counts[c] += 1

    lines = [f"# Eval diff — {experiment}" + (f"  (vs {baseline})" if baseline else ""), ""]
    lines += [
        "> Honest-reading notes: `FRAGMENTATION?` is a heuristic, not a verdict. "
        "A high `OTHER` count means diagnosis coverage is weak (ground truth named no "
        "provision), NOT that those questions passed. `IN_SCOPE_ABSTAIN (justified)` "
        "means retrieval also missed, so abstaining was reasonable; `(gate)` means the "
        "sources were present and the gate abstained anyway.", "",
        "## Failure-class counts", "",
    ]
    if baseline:
        lines += ["| class | exp | base | Δ |", "|---|--:|--:|--:|"]
        for c in CLASS_ORDER:
            dv = exp_counts[c] - base_counts[c]
            lines.append(f"| {c} | {exp_counts[c]} | {base_counts[c]} | {dv:+d} |")
    else:
        lines += ["| class | n |", "|---|--:|"]
        for c in CLASS_ORDER:
            lines.append(f"| {c} | {exp_counts[c]} |")

    lines += ["", "## Per-question", "",
              "| question | cat | class | recall | Δrec | exp_src | got_prov | answer |",
              "|---|---|---|--:|--:|---|---|---|"]
    for r in rows:
        dr = f"{r['d_recall']:+.2f}" if r["d_recall"] is not None else "—"
        lines.append(
            f"| {_trunc(r['question'], 48)} | {r['category']} | {r['display']} | "
            f"{_md_num(r['recall'])} | {dr} | {r['exp_src']} | {r['got_prov']} | "
            f"{_trunc(r['answer'], 60)} |"
        )

    out_path = Path(out) if out else Path(settings.eval_results_dir) / "diffs" / f"diff_{experiment}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return out_path
