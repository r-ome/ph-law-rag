from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes_query import Source
from app.retriever.answer_service import run_answer

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class ChunkTrace(BaseModel):
    chunk_id: str = ""
    score: float | None = None
    source_id: str = ""
    unit_label: str = ""
    provision_id: str = ""
    expanded_from_parent: bool = False
    consolidated: str = ""
    dedup_merged_chunk_ids: list[str] = []
    preview: str = ""


class TraceRecord(BaseModel):
    trace_id: str = ""
    trace_label: str | None = None
    timestamp: str | None = None
    session_id: str | None = None
    question: str = ""
    rewritten_question: str = ""
    stage_counts: dict[str, int] = {}
    retrieved_chunks: list[ChunkTrace] = []
    pre_expansion_chunks: list[ChunkTrace] = []
    selected_chunks: list[ChunkTrace] = []
    retrieval_strategy: dict[str, Any] = {}
    intent_router: dict[str, Any] = {}
    feature_flags: dict[str, Any] = {}
    abstained: bool = False
    error: bool = False
    stages: list[Any] = []
    latency_ms: float | None = None
    prompt_length: int | None = None
    generator_model: str | None = None


class InspectRequest(BaseModel):
    question: str
    strategy: Literal["default", "current_law"] | None = None


class InspectResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    abstained: bool = False
    error: bool = False
    error_message: str | None = None
    trace: TraceRecord | None = None


@router.post("/inspect", response_model=InspectResponse, summary="Run a query and inspect its trace")
def inspect(request: InspectRequest) -> InspectResponse:
    try:
        response, trace_record = run_answer(
            request.question,
            debug=True,
            session_id=None,
            trace=True,
            trace_label="lab",
            strategy_override=request.strategy,
        )
    except Exception as e:
        return InspectResponse(
            answer="", sources=[], abstained=False, error=True,
            error_message=str(e), trace=None,
        )
    return InspectResponse(
        answer=response["answer"],
        sources=response.get("sources", []),
        abstained=response.get("abstained", False),
        error=response.get("error", False),
        trace=TraceRecord(**trace_record) if trace_record else None,
    )
