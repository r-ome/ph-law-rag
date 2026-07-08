from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes_stats import SyncRunSummary
from app.db import list_sync_runs

router = APIRouter(prefix="/sync", tags=["sync"])


class SyncRunListResponse(BaseModel):
    runs: list[SyncRunSummary]


@router.get("/runs", response_model=SyncRunListResponse, summary="Recent sync runs")
def runs(limit: int = 20) -> SyncRunListResponse:
    return SyncRunListResponse(runs=list_sync_runs(limit))
