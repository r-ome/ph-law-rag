import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.api.routes_retrieval import InspectRequest
from app.evals import retrieval_comparison
from app.evals import retrieval_runner
from app.evals.sibling_census import build_sibling_eligibility_census
from app.observability.context import TraceCollector
from app.pipeline.runner import _retrieval_stage_timings_ms

pytestmark = pytest.mark.unit


def test_all_sibling_knobs_participate_in_sealed_selection_identity():
    sibling_keys = {
        "sibling_expansion_enabled",
        "sibling_expansion_radius",
        "sibling_expansion_max_chars",
        "sibling_expansion_max_tokens",
    }
    assert sibling_keys <= set(retrieval_comparison._SELECTION_KEYS)

    defaults = {key: None for key in retrieval_comparison._SELECTION_KEYS}
    baseline = retrieval_comparison._identity_parts(
        {}, {"shared_values": {"retrieval_defaults": defaults}}
    )
    for key in sibling_keys:
        candidate = retrieval_comparison._identity_parts(
            {},
            {
                "shared_values": {
                    "retrieval_defaults": {**defaults, key: "changed"}
                }
            },
        )
        assert baseline["selection"] != candidate["selection"]


def test_sibling_latency_is_reported_separately_and_in_expanded_total():
    collector = TraceCollector()
    collector.stage("sibling_expansion", ms=1.25)

    timings = _retrieval_stage_timings_ms(collector)

    assert timings["sibling_expansion"] == 1.25
    assert timings["expanded"] == 1.25


def test_retrieval_lab_accepts_only_explicit_registered_strategies():
    assert InspectRequest(question="q", strategy="sibling_aware").strategy == "sibling_aware"
    with pytest.raises(ValidationError):
        InspectRequest(question="q", strategy="not_registered")


def test_retrieval_only_harness_accepts_explicit_sibling_strategy(monkeypatch, tmp_path):
    captured = {}
    expected = tmp_path / "sealed.jsonl"

    def fake_capture(*args, **kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(retrieval_runner, "_retrieve_rows_capture", fake_capture)

    result = retrieval_runner.retrieve_rows(
        [{"id": "eval_x", "question": "q", "split": "dev"}],
        tag="sibling",
        strategy_override="sibling_aware",
    )

    assert result == expected
    assert captured["strategy_override"] == "sibling_aware"
    assert captured["policy"].retrieval_defaults.sibling_expansion_enabled is True


def _census_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE chunks(chunk_id TEXT, chunk_index INTEGER, char_count INTEGER, "
        "token_estimate INTEGER, metadata_json TEXT)"
    )
    for chunk_id, index, label in (
        ("d", 1586, "Article 1403(2)(d)"),
        ("e", 1587, "Article 1403(2)(e)"),
    ):
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?)",
            (
                chunk_id,
                index,
                10,
                3,
                json.dumps(
                    {
                        "source_id": "civil_code",
                        "provision_id": "civil_code:article:1403",
                        "parent_key": "civil_code::CHAPTER 8::Article 1403",
                        "unit_label": label,
                    }
                ),
            ),
        )
    conn.commit()
    conn.close()


def test_read_only_census_finds_eval_053_radius_one_and_declares_small_n_descriptive(tmp_path):
    db_path = tmp_path / "chunks.db"
    _census_db(db_path)
    trace_path = tmp_path / "retrieval_trace.jsonl"
    records = [
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "split": "regression",
            "stage": "reranked",
            "rank": 3,
            "chunk_id": "d",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(d)",
            "survived": True,
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "split": "regression",
            "stage": "expanded",
            "snapshot_ordinal": 2,
            "rank": 3,
            "chunk_id": "d",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(d)",
        },
        {"record_type": "row_complete", "eval_id": "eval_053"},
    ]
    trace_path.write_text("".join(json.dumps(record) + "\n" for record in records))
    targets = {
        "eval_053": {
            "eval_id": "eval_053",
            "match_mode": "exact",
            "targets": [
                {
                    "source_id": "civil_code",
                    "provision_id": "civil_code:article:1403",
                    "unit_label": "Article 1403(2)(e)",
                }
            ],
        }
    }

    census = build_sibling_eligibility_census(
        trace_path, targets=targets, db_path=db_path
    )

    assert census["eligible_missed_rows"] == 1
    assert census["eligible_eval_ids"] == ["eval_053"]
    assert census["gate_mode"] == "descriptive"
    assert census["details"][0]["sibling_offset"] == 1
    assert census["eligible_recovery_rate"] == 0.0


def test_census_ignores_targets_for_rows_not_completed_in_the_trace(tmp_path):
    db_path = tmp_path / "chunks.db"
    _census_db(db_path)
    trace_path = tmp_path / "retrieval_trace.jsonl"
    trace_path.write_text(
        json.dumps({"record_type": "row_complete", "eval_id": "eval_other"}) + "\n"
    )
    targets = {
        "eval_053": {
            "eval_id": "eval_053",
            "match_mode": "exact",
            "targets": [
                {
                    "source_id": "civil_code",
                    "provision_id": "civil_code:article:1403",
                    "unit_label": "Article 1403(2)(e)",
                }
            ],
        }
    }

    census = build_sibling_eligibility_census(
        trace_path, targets=targets, db_path=db_path
    )

    assert census["missed_exact_leaf_rows"] == 0
    assert census["eligible_missed_rows"] == 0


def test_census_measures_eligible_recovery_and_selected_retention(tmp_path):
    db_path = tmp_path / "chunks.db"
    _census_db(db_path)
    trace_path = tmp_path / "retrieval_trace.jsonl"
    records = [
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "stage": "reranked",
            "snapshot_ordinal": 1,
            "rank": 3,
            "chunk_id": "d",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(d)",
            "survived": True,
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "stage": "expanded",
            "snapshot_ordinal": 2,
            "rank": 3,
            "chunk_id": "d",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(d)",
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "stage": "expanded",
            "snapshot_ordinal": 2,
            "rank": 4,
            "chunk_id": "e",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(e)",
            "expanded_from_sibling": True,
        },
        {
            "record_type": "candidate",
            "eval_id": "eval_053",
            "stage": "selected",
            "snapshot_ordinal": 3,
            "rank": 4,
            "chunk_id": "e",
            "source_id": "civil_code",
            "provision_id": "civil_code:article:1403",
            "unit_label": "Article 1403(2)(e)",
            "expanded_from_sibling": True,
        },
        {"record_type": "row_complete", "eval_id": "eval_053"},
    ]
    trace_path.write_text("".join(json.dumps(record) + "\n" for record in records))
    targets = {
        "eval_053": {
            "eval_id": "eval_053",
            "match_mode": "exact",
            "targets": [
                {
                    "source_id": "civil_code",
                    "provision_id": "civil_code:article:1403",
                    "unit_label": "Article 1403(2)(e)",
                }
            ],
        }
    }

    census = build_sibling_eligibility_census(
        trace_path, targets=targets, db_path=db_path
    )

    assert census["eligible_recovered_at_expanded_rows"] == 1
    assert census["eligible_recovered_at_selected_rows"] == 1
    assert census["eligible_recovery_rate"] == 1.0
    assert census["eligible_recovered_eval_ids"] == ["eval_053"]


def test_census_rejects_holdout_trace(tmp_path):
    trace_path = tmp_path / "retrieval_trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "record_type": "candidate",
                "eval_id": "sealed",
                "split": "holdout",
                "stage": "reranked",
            }
        )
        + "\n"
        + json.dumps({"record_type": "row_complete", "eval_id": "sealed"})
        + "\n"
    )

    with pytest.raises(ValueError, match="holdout"):
        build_sibling_eligibility_census(trace_path, targets={})
