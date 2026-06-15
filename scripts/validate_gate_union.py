"""Fast gate validation via union — NO regeneration, NO haiku.

combined_abstain[q] = gate_says_NO[q] OR generator_self_abstained[q]

The generator's self-abstention per question (gate OFF) is read from a prior
baseline run. We only compute the revised gate's YES/NO per question (retrieval +
one mistral gate call, no generation), then union the two. Isolates the gate's
effect and controls for mistral temp-0 nondeterminism by holding generation fixed.

    PYTHONUNBUFFERED=1 uv run python scripts/validate_gate_union.py <baseline_gate_off.jsonl>
"""
import json
import sys
from collections import defaultdict

from app.config import settings
from app.retriever.hybrid_retriever import hybrid_retriever
from app.retriever.reranker import rerank
from app.retriever.edge_expansion import expand_with_edges
from app.retriever.answerability import is_answerable


def _ctx(question: str):
    reranked = rerank(question, hybrid_retriever(question))
    if settings.edge_expansion_enabled:
        reranked = expand_with_edges(question, reranked)
    return reranked


def main(baseline_path: str):
    base = {json.loads(l)["question"]: json.loads(l)
            for l in open(baseline_path) if l.strip()}
    rows = [json.loads(l) for l in open(settings.eval_dataset_path) if l.strip()]

    by = defaultdict(lambda: [0, 0])  # [abstained, total]
    oos_leak, nonoos_ab, gate_no = [], [], []
    base_correct = 0

    for i, item in enumerate(rows, 1):
        q = item["question"]
        cat = item["category"]
        gate_ok = is_answerable(q, _ctx(q))           # True = answerable
        gen_abst = base.get(q, {}).get("abstained", False)
        combined_abst = (not gate_ok) or gen_abst

        if not gate_ok:
            gate_no.append((cat, q, gen_abst))
        by[cat][1] += 1
        if combined_abst:
            by[cat][0] += 1
        if cat == "out-of-scope" and not combined_abst:
            oos_leak.append(q)
        if cat != "out-of-scope" and combined_abst:
            nonoos_ab.append((cat, q, gen_abst))
        # baseline (gate-off) correctness, for the headline delta
        base_good = gen_abst if cat == "out-of-scope" else not gen_abst
        base_correct += base_good
        print(f"[{i}/{len(rows)}] {cat:13} gate={'YES' if gate_ok else 'NO ':3} "
              f"gen_abstain={gen_abst} -> combined_abstain={combined_abst}", flush=True)

    print("\n=== COMBINED ABSTENTION by category (abstained/total) ===")
    correct = 0
    for cat, (ab, tot) in sorted(by.items()):
        good = ab if cat == "out-of-scope" else tot - ab
        correct += good
        print(f"  {cat:13} abstained {ab}/{tot}  correct {good}/{tot}")
    print(f"  REVISED-GATE combined correct: {correct}/{len(rows)}")
    print(f"  BASELINE (gate off) correct:   {base_correct}/{len(rows)}")

    print(f"\n=== gate said NO on {len(gate_no)} questions ===")
    for cat, q, gen in gate_no:
        tag = "(gen already abstained)" if gen else "(NEW abstain from gate)"
        print(f"  - [{cat}] {tag} {q}")
    print(f"\n=== remaining OOS leaks: {len(oos_leak)} ===")
    for q in oos_leak:
        print(f"  - {q}")
    print(f"\n=== non-OOS abstains (false-abstain risk): {len(nonoos_ab)} ===")
    for cat, q, gen in nonoos_ab:
        src = "gen" if gen and not any(g[1] == q and not g[2] for g in gate_no) else "gate"
        print(f"  - [{cat}] (from {src}) {q}")


if __name__ == "__main__":
    main(sys.argv[1])
