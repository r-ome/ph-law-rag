from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_retrieval import TraceRecord
from app.trace_store import get_trace, list_traces

router = APIRouter(prefix="/traces", tags=["traces"])


class TraceSummary(BaseModel):
    trace_id: str = ""
    timestamp: str | None = None
    trace_label: str | None = None
    question: str = ""
    strategy: str | None = None
    stage_counts: dict[str, int] = {}
    latency_ms: float | None = None
    abstained: bool = False
    error: bool = False


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]


@router.get("", response_model=TraceListResponse, summary="Recent trace summaries")
def traces(limit: int = 50, date: str | None = None) -> TraceListResponse:
    return TraceListResponse(traces=list_traces(limit=limit, date=date))


@router.get("/{trace_id}", response_model=TraceRecord, summary="Full trace record by id")
def trace_detail(trace_id: str) -> TraceRecord:
    rec = get_trace(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return TraceRecord(**rec)
