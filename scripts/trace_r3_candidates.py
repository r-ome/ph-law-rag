from __future__ import annotations

import argparse
import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.evals.intent_labels import load_intent_labels
from app.retriever.strategy import RetrievalKnobs, resolve_knobs
from app.retriever.types import RetrievalResult


INTENDED_BASELINE = {
    "dense_top_k": 30,
    "sparse_top_k": 10,
    "rerank_top_n": 8,
    "parent_expansion_enabled": True,
    "prefer_operative_enabled": False,
    "retrieval_operative_only": True,
    "consolidated_dedup_enabled": True,
}


@dataclass(frozen=True)
class Target:
    source_id: str
    provision_id: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class Probe:
    question_id: str
    intent: Literal["citation_lookup", "amendment_or_current_law"]
    origin: Literal["eval", "synthetic"]
    question: str
    targets: tuple[Target, ...]


@dataclass(frozen=True)
class Hit:
    present: bool
    rank: int | None = None
    chunk_id: str | None = None
    source_id: str | None = None
    provision_id: str | None = None
    unit_label: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class Arm:
    name: str
    knobs: RetrievalKnobs


@dataclass
class ArmTrace:
    probe: Probe
    arm: Arm
    stages: dict[str, list[RetrievalResult]]
    sanity_ok: bool


CITATION_TARGETS: dict[str, tuple[Target, ...]] = {
    "What is the penalty for possession of dangerous drugs under RA 9165?": (
        Target("dangerous_drugs_act", "dangerous_drugs_act:article-ii:section:11"),
    ),
    "What penalty does RA 9165 impose for the sale of dangerous drugs?": (
        Target("dangerous_drugs_act", "dangerous_drugs_act:article-ii:section:5"),
    ),
    "What is the penalty for issuing a bouncing check under BP 22?": (
        Target("bp_22", "bp_22:section:1"),
    ),
    "What is Alternative Dispute Resolution under RA 9285?": (
        Target("adr_act", "adr_act:section:3"),
    ),
}


AMENDMENT_TARGETS: dict[str, tuple[Target, ...]] = {
    "Have the property values used to determine penalties under the Revised Penal Code been updated, and by what law?": (
        Target("rpc_penalty_amendments_2017"),
    ),
    "Who must be present during the inventory and photographing of seized dangerous drugs under current law?": (
        Target("dangerous_drugs_amendments_2014", "dangerous_drugs_act:section:21"),
        Target("dangerous_drugs_amendments_2014", "dangerous_drugs_amendments_2014:section:1"),
    ),
    "How is the penalty for theft determined under the Revised Penal Code today, and how has that changed?": (
        Target("rpc_penalty_amendments_2017", "revised_penal_code:article:309"),
    ),
    "What are the chain-of-custody requirements for seized drugs, and how were they relaxed from the original Comprehensive Dangerous Drugs Act?": (
        Target("dangerous_drugs_amendments_2014", "dangerous_drugs_act:section:21"),
        Target("dangerous_drugs_amendments_2014", "dangerous_drugs_amendments_2014:section:1"),
    ),
    "What age threshold did RA 11648 set for statutory rape in the Philippines, even without force or intimidation?": (
        Target("statutory_rape_amendments_2022", "revised_penal_code:article:266-a"),
    ),
    "Is there an exception to statutory rape when the offender and the minor are close in age?": (
        Target("statutory_rape_amendments_2022", "revised_penal_code:article:266-a"),
    ),
    "My cousin is 15 and her boyfriend is 17 \u2014 if they sleep together, is he committing statutory rape?": (
        Target("statutory_rape_amendments_2022", "revised_penal_code:article:266-a"),
    ),
    "How did the Anti-Rape Law of 1997 change the treatment of rape under the Revised Penal Code?": (
        Target("anti_rape_law_1997", "revised_penal_code:article:266-a"),
    ),
    "Is rape still prosecuted under Article 335 of the Revised Penal Code?": (
        Target("anti_rape_law_1997", "revised_penal_code:article:266-a"),
        Target("statutory_rape_amendments_2022", "revised_penal_code:article:266-a"),
    ),
    "Several provisions of the Revised Penal Code still prescribe the death penalty. Can Philippine courts impose it, and what penalty is imposed instead?": (
        Target("death_penalty_prohibition", "death_penalty_prohibition:section:1"),
        Target("death_penalty_prohibition", "death_penalty_prohibition:section:2"),
    ),
    "Is a person whose death sentence was reduced to reclusion perpetua eligible for parole?": (
        Target("death_penalty_prohibition", "death_penalty_prohibition:section:3"),
    ),
    "Is trafficking in persons committed during a pandemic or other public emergency treated more severely?": (
        Target("anti_trafficking_amendments_2022", "anti_trafficking:section:6"),
    ),
    "Can a defendant who appealed their conviction still apply for probation?": (
        Target("probation_amendments_2015", "probation_law:section:4"),
    ),
}


SYNTHETIC_CITATION_PROBES: tuple[Probe, ...] = (
    Probe(
        question_id="synthetic_citation_rpc_art_308",
        intent="citation_lookup",
        origin="synthetic",
        question="What does Article 308 of the Revised Penal Code say?",
        targets=(Target("revised_penal_code", "revised_penal_code:article:308"),),
    ),
    Probe(
        question_id="synthetic_citation_ra9165_sec_5",
        intent="citation_lookup",
        origin="synthetic",
        question="What does Section 5 of RA 9165 provide?",
        targets=(Target("dangerous_drugs_act", "dangerous_drugs_act:article-ii:section:5"),),
    ),
    Probe(
        question_id="synthetic_citation_const_art_iii_sec_13",
        intent="citation_lookup",
        origin="synthetic",
        question="What does Section 13, Article III of the 1987 Constitution say?",
        targets=(Target("constitution_1987", "constitution_1987:article-iii:section:13"),),
    ),
    Probe(
        question_id="synthetic_citation_civil_code_art_1157",
        intent="citation_lookup",
        origin="synthetic",
        question="What does Article 1157 of the Civil Code say?",
        targets=(Target("civil_code", "civil_code:article:1157"),),
    ),
)


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _clone(results: list[RetrievalResult]) -> list[RetrievalResult]:
    return [
        RetrievalResult(
            chunk_id=result.chunk_id,
            text=result.text,
            score=result.score,
            metadata=dict(result.metadata),
        )
        for result in results
    ]


def _ids(results: list[RetrievalResult]) -> list[str]:
    return [result.chunk_id for result in results]


def _matches(result: RetrievalResult, targets: tuple[Target, ...]) -> bool:
    source_id = result.metadata.get("source_id")
    provision_id = result.metadata.get("provision_id")
    for target in targets:
        if source_id != target.source_id:
            continue
        if target.provision_id is None or provision_id == target.provision_id:
            return True
    return False


def _first_hit(results: list[RetrievalResult], targets: tuple[Target, ...]) -> Hit:
    for rank, result in enumerate(results, start=1):
        if _matches(result, targets):
            return Hit(
                present=True,
                rank=rank,
                chunk_id=result.chunk_id,
                source_id=result.metadata.get("source_id"),
                provision_id=result.metadata.get("provision_id"),
                unit_label=result.metadata.get("unit_label"),
                score=result.score,
            )
    return Hit(False)


def _hit_dict(hit: Hit) -> dict[str, Any]:
    return asdict(hit)


def _target_dicts(targets: tuple[Target, ...]) -> list[dict[str, str | None]]:
    return [asdict(target) for target in targets]


@contextmanager
def _temporary_reranker_backend(backend: str):
    previous = settings.reranker_backend
    settings.reranker_backend = backend
    try:
        yield
    finally:
        settings.reranker_backend = previous


def _dense_deep(question: str, knobs: RetrievalKnobs) -> list[RetrievalResult]:
    from app.retriever.dense_retriever import dense_retriever

    return dense_retriever(question, top_k=50, knobs=knobs)


def _sparse_deep(question: str, knobs: RetrievalKnobs) -> list[RetrievalResult]:
    from app.retriever.sparse_retriever import sparse_retriever

    return sparse_retriever(question, knobs=replace(knobs, sparse_top_k=50))


def _run_pipeline(question: str, knobs: RetrievalKnobs) -> dict[str, list[RetrievalResult]]:
    from app.retriever.context_selection import select_context
    from app.retriever.dedup import dedup_results
    from app.retriever.edge_expansion import expand_with_edges
    from app.retriever.hybrid_retriever import hybrid_retriever
    from app.retriever.parent_expansion import expand_parents
    from app.retriever.prefer_operative import prefer_operative
    from app.retriever.reranker import rerank

    if settings.subquery_packaging_enabled:
        raise RuntimeError(
            "R3 trace expects the standard hybrid path; set subquery_packaging_enabled=false."
        )

    stages: dict[str, list[RetrievalResult]] = {}
    stages["dense50"] = _dense_deep(question, knobs)
    stages["sparse50"] = _sparse_deep(question, knobs)
    stages["fused"] = hybrid_retriever(question, knobs=knobs)
    stages["reranked"] = rerank(question, _clone(stages["fused"]), knobs=knobs)

    if settings.edge_expansion_enabled:
        stages["edge_expanded"] = expand_with_edges(question, _clone(stages["reranked"]), knobs=knobs)
    else:
        stages["edge_expanded"] = _clone(stages["reranked"])

    stages["prefer_operative"] = prefer_operative(_clone(stages["edge_expanded"]), knobs=knobs)

    if knobs.parent_expansion_enabled:
        stages["parent_expanded"] = expand_parents(_clone(stages["prefer_operative"]), knobs=knobs)
    else:
        stages["parent_expanded"] = _clone(stages["prefer_operative"])

    if knobs.consolidated_dedup_enabled:
        stages["selected"] = dedup_results(_clone(stages["parent_expanded"]))
    else:
        stages["selected"] = _clone(stages["parent_expanded"])

    real = select_context(question, knobs=knobs)
    stages["_select_context_selected"] = real.selected
    return stages


def _trace_arm(probe: Probe, arm: Arm) -> ArmTrace:
    stages = _run_pipeline(probe.question, arm.knobs)
    sanity_ok = _ids(stages["selected"]) == _ids(stages["_select_context_selected"])
    return ArmTrace(probe=probe, arm=arm, stages=stages, sanity_ok=sanity_ok)


def _stage_rows(trace: ArmTrace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage, results in trace.stages.items():
        if stage.startswith("_"):
            continue
        hit = _first_hit(results, trace.probe.targets)
        rows.append(
            {
                "question_id": trace.probe.question_id,
                "origin": trace.probe.origin,
                "intent": trace.probe.intent,
                "question": trace.probe.question,
                "arm": trace.arm.name,
                "stage": stage,
                "count": len(results),
                "target": _target_dicts(trace.probe.targets),
                "hit": _hit_dict(hit),
                "knobs": trace.arm.knobs.as_trace_dict(),
                "sanity_ok": trace.sanity_ok,
            }
        )
    return rows


def _load_probes() -> list[Probe]:
    labels = load_intent_labels()
    dataset_rows = _read_jsonl(Path(settings.eval_dataset_path))
    probes: list[Probe] = []
    for index, row in enumerate(dataset_rows, start=1):
        question = row["question"]
        intent = labels[question]
        if intent == "citation_lookup":
            targets = CITATION_TARGETS.get(question)
            if targets is None:
                raise ValueError(f"missing citation target mapping for row {index}: {question}")
            probes.append(
                Probe(
                    question_id=f"eval_{index:03d}",
                    intent="citation_lookup",
                    origin="eval",
                    question=question,
                    targets=targets,
                )
            )
        elif intent == "amendment_or_current_law":
            targets = AMENDMENT_TARGETS.get(question)
            if targets is None:
                raise ValueError(f"missing amendment target mapping for row {index}: {question}")
            probes.append(
                Probe(
                    question_id=f"eval_{index:03d}",
                    intent="amendment_or_current_law",
                    origin="eval",
                    question=question,
                    targets=targets,
                )
            )
    return probes + list(SYNTHETIC_CITATION_PROBES)


def _make_arms(base: RetrievalKnobs, intent: str) -> list[Arm]:
    if intent == "citation_lookup":
        return [
            Arm("default", base),
            Arm("sparse20", replace(base, sparse_top_k=20)),
            Arm("sparse30", replace(base, sparse_top_k=30)),
        ]
    if intent == "amendment_or_current_law":
        return [
            Arm("default", base),
            Arm("prefer_operative", replace(base, prefer_operative_enabled=True)),
        ]
    raise ValueError(f"unsupported intent {intent}")


def _first_stale_hit(results: list[RetrievalResult], targets: tuple[Target, ...]) -> Hit:
    from app.retriever.supersession import load_supersessions, provision_matches

    target_pairs = {(target.source_id, target.provision_id) for target in targets}
    stale_targets: list[Target] = []
    for rule in load_supersessions():
        operative_pairs = {
            (rule.operative_source_id, provision_id)
            for provision_id in rule.operative_provision_ids
        }
        if not target_pairs & operative_pairs:
            continue
        stale_targets.extend(
            Target(rule.base_source_id, provision_id)
            for provision_id in rule.base_provision_ids
        )

    if not stale_targets:
        return Hit(False)

    for rank, result in enumerate(results, start=1):
        for target in stale_targets:
            if result.metadata.get("source_id") != target.source_id:
                continue
            provision_id = result.metadata.get("provision_id")
            if target.provision_id and provision_matches(provision_id, (target.provision_id,)):
                return Hit(
                    present=True,
                    rank=rank,
                    chunk_id=result.chunk_id,
                    source_id=result.metadata.get("source_id"),
                    provision_id=provision_id,
                    unit_label=result.metadata.get("unit_label"),
                    score=result.score,
                )
    return Hit(False)


def _citation_verdict(probe: Probe, traces: dict[str, ArmTrace]) -> dict[str, Any]:
    default_selected = _first_hit(traces["default"].stages["selected"], probe.targets)
    sparse50 = _first_hit(traces["default"].stages["sparse50"], probe.targets)
    arms: dict[str, Any] = {}
    improved_arms: list[str] = []
    for arm_name, sparse_limit in (("sparse20", 20), ("sparse30", 30)):
        selected = _first_hit(traces[arm_name].stages["selected"], probe.targets)
        outside_default_inside_arm = (
            sparse50.rank is not None and 10 < sparse50.rank <= sparse_limit
        )
        improved = (
            not default_selected.present
            and selected.present
            and outside_default_inside_arm
        )
        arms[arm_name] = {
            "selected_hit": _hit_dict(selected),
            "outside_default_inside_arm": outside_default_inside_arm,
            "improved": improved,
        }
        if improved:
            improved_arms.append(arm_name)
    return {
        "question_id": probe.question_id,
        "intent": probe.intent,
        "question": probe.question,
        "default_selected_hit": _hit_dict(default_selected),
        "default_sparse50_hit": _hit_dict(sparse50),
        "arms": arms,
        "registering_evidence": bool(improved_arms),
        "improved_arms": improved_arms,
    }


def _amendment_verdict(probe: Probe, traces: dict[str, ArmTrace]) -> dict[str, Any]:
    default_selected = traces["default"].stages["selected"]
    prefer_selected = traces["prefer_operative"].stages["selected"]
    default_target = _first_hit(default_selected, probe.targets)
    prefer_target = _first_hit(prefer_selected, probe.targets)
    default_stale = _first_stale_hit(default_selected, probe.targets)
    prefer_stale = _first_stale_hit(prefer_selected, probe.targets)

    default_stale_above = (
        default_target.rank is not None
        and default_stale.rank is not None
        and default_stale.rank < default_target.rank
    )
    prefer_demoted = (
        prefer_target.rank is not None
        and prefer_stale.rank is not None
        and prefer_target.rank < prefer_stale.rank
    )
    default_edge = traces["default"].stages["edge_expanded"]
    prefer_edge = traces["prefer_operative"].stages["prefer_operative"]
    prefer_changed_post_cut = _ids(default_edge) != _ids(prefer_edge)
    regression = default_target.present and not prefer_target.present

    return {
        "question_id": probe.question_id,
        "intent": probe.intent,
        "question": probe.question,
        "default_target_hit": _hit_dict(default_target),
        "prefer_target_hit": _hit_dict(prefer_target),
        "default_stale_hit": _hit_dict(default_stale),
        "prefer_stale_hit": _hit_dict(prefer_stale),
        "default_stale_above_target": default_stale_above,
        "prefer_operative_changed_post_cut_order": prefer_changed_post_cut,
        "prefer_operative_demoted_stale_below_target": prefer_demoted,
        "regression": regression,
        "registering_evidence": default_stale_above and prefer_changed_post_cut and prefer_demoted,
    }


def _summarize(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    citation = [v for v in verdicts if v["intent"] == "citation_lookup"]
    amendment = [v for v in verdicts if v["intent"] == "amendment_or_current_law"]
    citation_evidence = [v["question_id"] for v in citation if v["registering_evidence"]]
    amendment_evidence = [v["question_id"] for v in amendment if v["registering_evidence"]]
    amendment_regressions = [v["question_id"] for v in amendment if v.get("regression")]
    return {
        "citation_precision": {
            "register": bool(citation_evidence),
            "evidence_question_ids": citation_evidence,
            "rule": (
                "Register only when sparse_top_k=20/30 improves selected-target "
                "outcome via a target outside sparse top 10 but inside the deeper cut."
            ),
        },
        "current_law": {
            "register": bool(amendment_evidence) and not amendment_regressions,
            "evidence_question_ids": amendment_evidence,
            "regression_question_ids": amendment_regressions,
            "rule": (
                "Register only when prefer_operative demotes a stale mapped chunk below "
                "the operative target after the real cut on at least one row, with no regression."
            ),
        },
    }


def _print_table(verdicts: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    print("\nR3 candidate verdicts")
    print("candidate             register  evidence  regressions")
    print(
        "citation_precision    "
        f"{str(summary['citation_precision']['register']):<8}  "
        f"{','.join(summary['citation_precision']['evidence_question_ids']) or '-':<8}  -"
    )
    print(
        "current_law           "
        f"{str(summary['current_law']['register']):<8}  "
        f"{','.join(summary['current_law']['evidence_question_ids']) or '-':<8}  "
        f"{','.join(summary['current_law']['regression_question_ids']) or '-'}"
    )

    print("\nPer-row verdicts")
    print("question_id  intent                  evidence  note")
    for verdict in verdicts:
        if verdict["intent"] == "citation_lookup":
            note = ",".join(verdict["improved_arms"]) or "no sparse-depth improvement"
        else:
            note = (
                "prefer-op demoted stale"
                if verdict["registering_evidence"]
                else "no mapped stale demotion"
            )
            if verdict.get("regression"):
                note += "; regression"
        print(
            f"{verdict['question_id']:<11}  "
            f"{verdict['intent']:<22}  "
            f"{str(verdict['registering_evidence']):<8}  "
            f"{note}"
        )


def _create_run_dir() -> Path:
    started_at = datetime.now()
    tag = f"r3_trace_{started_at.strftime('%Y%m%d_%H%M%S')}"
    return Path(settings.eval_results_dir) / "runs" / started_at.strftime("%Y-%m-%d") / tag


def _assert_baseline(base: RetrievalKnobs, allow_nonstandard: bool) -> None:
    actual = base.as_trace_dict()
    if actual == INTENDED_BASELINE:
        return
    if allow_nonstandard:
        return
    raise SystemExit(
        "default strategy knobs do not match the intended R3 baseline. "
        f"actual={actual} intended={INTENDED_BASELINE}. "
        "Use --allow-nonstandard-baseline only for an intentional sweep."
    )


def _run_traces(
    probes: list[Probe],
    base: RetrievalKnobs,
    trace_path: Path,
    *,
    backend: str,
) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    with _temporary_reranker_backend(backend):
        for probe in probes:
            arm_traces: dict[str, ArmTrace] = {}
            for arm in _make_arms(base, probe.intent):
                print(f"[TRACE] {probe.question_id} {arm.name} ({backend})")
                trace = _trace_arm(probe, arm)
                if not trace.sanity_ok:
                    raise RuntimeError(
                        f"instrumented pipeline drifted from select_context for "
                        f"{probe.question_id}/{arm.name}"
                    )
                arm_traces[arm.name] = trace
                _append_jsonl(trace_path, _stage_rows(trace))

            if probe.intent == "citation_lookup":
                verdicts.append(_citation_verdict(probe, arm_traces))
            else:
                verdicts.append(_amendment_verdict(probe, arm_traces))
    return verdicts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trace R3 strategy-router preset candidates with counterfactual knob arms."
    )
    parser.add_argument(
        "--allow-nonstandard-baseline",
        action="store_true",
        help="Run even if resolve_knobs('default') differs from the intended R3 baseline.",
    )
    parser.add_argument(
        "--bedrock-spot-check",
        nargs="*",
        default=[],
        metavar="QUESTION_ID",
        help="Optional question IDs to rerun under the bedrock reranker after the minilm run.",
    )
    args = parser.parse_args()

    base = resolve_knobs("default")
    _assert_baseline(base, args.allow_nonstandard_baseline)

    probes = _load_probes()
    run_dir = _create_run_dir()
    meta = {
        "kind": "r3_trace_candidates",
        "created_at": datetime.now().isoformat(),
        "git_sha": _git_sha(),
        "baseline_knobs": base.as_trace_dict(),
        "intended_baseline": INTENDED_BASELINE,
        "forced_main_reranker_backend": "minilm",
        "bedrock_spot_check_question_ids": args.bedrock_spot_check,
        "question_count": len(probes),
        "citation_count": sum(1 for probe in probes if probe.intent == "citation_lookup"),
        "amendment_count": sum(1 for probe in probes if probe.intent == "amendment_or_current_law"),
        "notes": [
            "No latest.json or manifest.jsonl writes; this is a trace artifact, not a RAGAS run.",
            "Sanity compares ordered selected chunk_ids against select_context output.",
        ],
    }
    _write_json(run_dir / "meta.json", meta)

    verdicts = _run_traces(probes, base, run_dir / "trace.jsonl", backend="minilm")
    _write_json(run_dir / "row_verdicts.json", verdicts)
    summary = _summarize(verdicts)
    _write_json(run_dir / "summary.json", summary)

    if args.bedrock_spot_check:
        by_id = {probe.question_id: probe for probe in probes}
        missing = [question_id for question_id in args.bedrock_spot_check if question_id not in by_id]
        if missing:
            raise SystemExit(f"unknown --bedrock-spot-check question id(s): {missing}")
        bedrock_verdicts = _run_traces(
            [by_id[question_id] for question_id in args.bedrock_spot_check],
            base,
            run_dir / "bedrock_trace.jsonl",
            backend="bedrock",
        )
        _write_json(run_dir / "bedrock_row_verdicts.json", bedrock_verdicts)

    _print_table(verdicts, summary)
    print(f"\nArtifacts: {run_dir}")


if __name__ == "__main__":
    main()
