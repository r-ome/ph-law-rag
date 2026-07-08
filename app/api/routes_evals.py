from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.eval_store import diff_runs, get_rows, get_run, list_runs

router = APIRouter(prefix="/evals/runs", tags=["evals"])


class MetricSet(BaseModel):
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRunSummary(BaseModel):
    tag: str
    date: str | None = None
    model: str | None = None
    label: str | None = None
    questions: int | None = None
    scored: int | None = None
    abstention_accuracy: float | None = None
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRunListResponse(BaseModel):
    runs: list[EvalRunSummary]


class Abstention(BaseModel):
    correct: int | None = None
    total: int | None = None
    accuracy: float | None = None


class CategoryMetrics(MetricSet):
    n: int | None = None


class EvalSummary(BaseModel):
    overall: MetricSet
    abstention: Abstention
    by_category: dict[str, CategoryMetrics] = {}


class EvalRunDetail(BaseModel):
    tag: str
    model: str | None = None
    label: str | None = None
    date: str | None = None
    git_sha: str | None = None
    question_count: int | None = None
    scored_count: int | None = None
    summary: EvalSummary | None = None
    meta: dict[str, Any] | None = None


class EvalRow(BaseModel):
    eval_id: str | None = None
    question: str
    answer: str
    category: str | None = None
    abstained: bool = False
    ground_truth: str | None = None
    contexts: list[str] = []
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None


class EvalRowsResponse(BaseModel):
    tag: str
    row_count: int
    scored_count: int
    rows: list[EvalRow]


class OverallDiff(BaseModel):
    candidate: MetricSet
    baseline: MetricSet
    delta: MetricSet


class AbstentionDiff(BaseModel):
    candidate: float | None = None
    baseline: float | None = None
    delta: float | None = None


class CategoryDiff(BaseModel):
    status: Literal["matched", "missing_baseline", "missing_candidate"]
    candidate: MetricSet | None = None
    baseline: MetricSet | None = None
    delta: MetricSet | None = None


class EvalDiff(BaseModel):
    candidate_tag: str
    baseline_tag: str
    overall: OverallDiff
    abstention: AbstentionDiff
    by_category: dict[str, CategoryDiff] = {}


@router.get("", response_model=EvalRunListResponse, summary="List eval runs (manifest)")
def runs() -> EvalRunListResponse:
    return EvalRunListResponse(runs=list_runs())


@router.get("/{tag}", response_model=EvalRunDetail, summary="Eval run meta + summary")
def run_detail(tag: str) -> EvalRunDetail:
    d = get_run(tag)
    if d is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRunDetail(**d)


@router.get("/{tag}/rows", response_model=EvalRowsResponse, summary="Per-question rows (run ⨝ scored)")
def run_rows(tag: str) -> EvalRowsResponse:
    r = get_rows(tag)
    if r is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRowsResponse(**r)


@router.get("/{tag}/diff", response_model=EvalDiff, summary="Metrics diff vs a baseline run")
def run_diff(tag: str, baseline: str) -> EvalDiff:
    d = diff_runs(candidate=tag, baseline=baseline)
    if d is None:
        raise HTTPException(status_code=404, detail="eval run(s) not found")
    return EvalDiff(**d)
