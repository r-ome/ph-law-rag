from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_logs import LogEntry
from app.eval_store import diff_runs, eval_policy, get_rows, get_run, get_run_logs, list_runs

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
    holdout: bool = False
    git_sha: str | None = None
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
    abstain_count: int | None = None
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


class EvalStage(BaseModel):
    name: str
    in_n: int | None = None
    out_n: int | None = None
    ms: float | None = None
    fired: bool | None = None
    model: str | None = None
    prompt_length: int | None = None


class EvalEvidence(BaseModel):
    verdict: str | None = None
    method: str | None = None
    missing_facets: list[str] = []
    detail: dict[str, Any] | None = None


class EvalModelChoice(BaseModel):
    model: str | None = None
    reason: str | None = None


class EvalCorrective(BaseModel):
    enabled: bool | None = None
    fired: bool | None = None
    added_chunks: int | None = None
    baseline_selected_count: int | None = None
    post_selected_count: int | None = None
    max_added: int | None = None


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
    # debug passthrough (Phase 6)
    split: str | None = None
    topic: str | None = None
    facet: str | None = None
    profile: str | None = None
    generator_model: str | None = None
    elapsed_s: float | None = None
    expected_sources: list[str] = []
    retrieved_sources: list[str] = []
    cited_sources: list[str] = []
    expected_missing: list[str] = []
    selected_chunk_ids: list[str] = []
    evidence: EvalEvidence | None = None
    corrective_retrieval: EvalCorrective | None = None
    model_choice: EvalModelChoice | None = None
    debug_stages: list[EvalStage] = []


class EvalRowsResponse(BaseModel):
    tag: str
    row_count: int
    scored_count: int
    rows: list[EvalRow]
    holdout_redacted: bool = False


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


class EvalQualityBand(BaseModel):
    key: Literal["strong", "fair", "weak"]
    label: str
    min: float | None = None
    range: str


class EvalSplitPolicy(BaseModel):
    key: str
    name: str
    count: int
    plain: str


class EvalPolicyResponse(BaseModel):
    noise_floor: float
    quality_bands: list[EvalQualityBand]
    splits: list[EvalSplitPolicy]


@router.get("", response_model=EvalRunListResponse, summary="List eval runs (manifest)")
def runs() -> EvalRunListResponse:
    return EvalRunListResponse(runs=list_runs())


@router.get("/policy", response_model=EvalPolicyResponse, summary="Evaluation display policy")
def policy() -> EvalPolicyResponse:
    return EvalPolicyResponse(**eval_policy())


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


class EvalLogWindow(BaseModel):
    started_at: str | None = None
    completed_at: str | None = None


class EvalRunLogsResponse(BaseModel):
    tag: str
    window: EvalLogWindow | None = None
    entries: list[LogEntry]
    count: int
    truncated: bool = False
    holdout_redacted: bool = False


@router.get("/{tag}/logs", response_model=EvalRunLogsResponse,
            summary="App-log slice for the run's time window")
def run_logs(tag: str, level: str | None = None, limit: int = 2000) -> EvalRunLogsResponse:
    r = get_run_logs(tag, level=level, limit=limit)
    if r is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return EvalRunLogsResponse(**r)
