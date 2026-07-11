from app.config import SourceConfig
from app.evals.source_review import build_source_review_report


def _source(source_id: str, *, amends: list[str] | None = None, supersedes: list[str] | None = None) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        enabled=True,
        file_format="html",
        url=f"https://example.test/{source_id}",
        category="statute",
        doc_type="republic_act",
        title=source_id,
        status="operative",
        source_index="lawphil",
        amends=amends or [],
        supersedes=supersedes or [],
    )


def test_source_review_follows_amends_not_supersedes_and_redacts_holdout_rows():
    rows = [
        {
            "id": "eval_001",
            "split": "regression",
            "question": "visible question",
            "ground_truth": "visible truth",
            "expected_sources": ["base_law"],
            "category": "factual",
            "topic": "topic",
        },
        {
            "id": "eval_140",
            "split": "holdout",
            "question": "private holdout question",
            "ground_truth": "private holdout truth",
            "expected_sources": ["base_law"],
            "category": "factual",
            "topic": "topic",
        },
        {
            "id": "eval_141",
            "split": "holdout",
            "question": "not affected",
            "ground_truth": "not affected",
            "expected_sources": ["superseded_law"],
            "category": "factual",
            "topic": "topic",
        },
    ]

    report = build_source_review_report(
        sync_run_id="sync-1",
        changed_sources=[{"source_id": "amending_law", "status": "new"}],
        sources=[
            _source("amending_law", amends=["base_law"], supersedes=["superseded_law"]),
            _source("base_law"),
            _source("superseded_law"),
        ],
        dataset_rows=rows,
    )

    assert report["status"] == "ground_truth_review_required"
    assert report["changed_sources"][0]["amends"] == ["base_law"]
    assert [row["id"] for row in report["affected_rows"]] == ["eval_001", "eval_140"]
    non_holdout = report["affected_rows"][0]
    assert non_holdout["question"] == "visible question"
    holdout = report["affected_rows"][1]
    assert holdout == {
        "id": "eval_140",
        "split": "holdout",
        "holdout_redacted": True,
    }
    assert "private holdout question" not in str(report)
    assert "private holdout truth" not in str(report)


def test_source_review_handles_transitive_cycles_and_dedups_affected_rows():
    report = build_source_review_report(
        sync_run_id="sync-2",
        changed_sources=[{"source_id": "amending_law", "status": "changed"}],
        sources=[
            _source("amending_law", amends=["base_law"]),
            _source("base_law", amends=["older_law"]),
            _source("older_law", amends=["base_law"]),
        ],
        dataset_rows=[
            {
                "id": "eval_001",
                "split": "regression",
                "question": "q",
                "ground_truth": "g",
                "expected_sources": ["amending_law", "base_law", "older_law"],
                "category": "factual",
                "topic": "topic",
            },
            {
                "id": "eval_002",
                "split": "regression",
                "question": "q2",
                "ground_truth": "g2",
                "expected_sources": ["unrelated"],
                "category": "factual",
                "topic": "topic",
            },
        ],
    )

    assert report["changed_sources"][0]["amends"] == ["base_law", "older_law"]
    assert report["affected_row_count"] == 1
    assert [row["id"] for row in report["affected_rows"]] == ["eval_001"]
    assert report["affected_rows"][0]["matched_sources"] == ["amending_law", "base_law", "older_law"]
