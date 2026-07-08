import pytest

from scripts import run_r5_predicted_strategy as r5

pytestmark = pytest.mark.unit


def _row(eval_id, selected_ids, sources=None, stages=None):
    return {
        "eval_id": eval_id,
        "question": f"Question {eval_id}",
        "answer": f"Answer {eval_id}",
        "contexts": [f"Context {eval_id}"],
        "selected_chunk_ids": selected_ids,
        "context_sources": sources or [],
        "expected_sources": sources or [],
        "debug_stages": stages or [],
        "ground_truth": "truth",
        "category": "factual",
        "abstained": False,
    }


def test_context_universe_composition_keeps_misses_and_false_fires():
    rows_by_id = {
        "eval_001": _row("eval_001", [], ["source_a"]),
        "eval_002": _row("eval_002", [], ["source_b"]),
        "eval_003": _row("eval_003", [], ["source_c"]),
    }
    baseline_by_id = {
        "eval_001": _row("eval_001", ["base-1"], ["source_a"]),
        "eval_002": _row("eval_002", ["same-2"], ["source_b"]),
        "eval_003": _row("eval_003", ["base-3"], ["source_c"]),
    }
    current_by_id = {
        "eval_001": _row(
            "eval_001",
            ["current-1"],
            ["source_a"],
            [{"name": "prefer_operative", "fired": True}],
        ),
        "eval_002": _row(
            "eval_002",
            ["same-2"],
            ["source_b"],
            [{"name": "prefer_operative", "fired": False}],
        ),
        "eval_003": _row(
            "eval_003",
            ["current-3"],
            ["source_c"],
            [{"name": "prefer_operative", "fired": True}],
        ),
    }
    sweep_by_id = {
        "eval_001": {
            "gold_intent": "amendment_or_current_law",
            "routed_intent": "default",
        },
        "eval_002": {
            "gold_intent": "amendment_or_current_law",
            "routed_intent": "amendment_or_current_law",
        },
        "eval_003": {
            "gold_intent": "default",
            "routed_intent": "amendment_or_current_law",
        },
    }
    gold_current_ids = {"eval_001", "eval_002"}
    predicted_current_ids = {"eval_002", "eval_003"}
    candidate_ids = gold_current_ids | predicted_current_ids

    diff = r5.build_context_diff(
        candidate_ids,
        rows_by_id,
        baseline_by_id,
        current_by_id,
        sweep_by_id,
        gold_current_ids,
        predicted_current_ids,
    )
    universe_ids = {row["eval_id"] for row in diff if row["changed"]}

    assert universe_ids == {"eval_001", "eval_003"}
    assert [row for row in diff if row["eval_id"] == "eval_002"][0]["changed"] is False
    false_fire = [row for row in diff if row["eval_id"] == "eval_003"][0]
    assert false_fire["false_fire_changed"] is True

    oracle = r5.compose_arm_rows(
        universe_ids,
        baseline_by_id,
        current_by_id,
        universe_ids & gold_current_ids,
    )
    predicted = r5.compose_arm_rows(
        universe_ids,
        baseline_by_id,
        current_by_id,
        universe_ids & predicted_current_ids,
    )
    oracle_by_id = {row["eval_id"]: row for row in oracle}
    predicted_by_id = {row["eval_id"]: row for row in predicted}

    assert oracle_by_id["eval_001"]["selected_chunk_ids"] == ["current-1"]
    assert predicted_by_id["eval_001"]["selected_chunk_ids"] == ["base-1"]
    assert oracle_by_id["eval_003"]["selected_chunk_ids"] == ["base-3"]
    assert predicted_by_id["eval_003"]["selected_chunk_ids"] == ["current-3"]
