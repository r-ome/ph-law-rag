from fastapi import APIRouter
from pydantic import BaseModel

from app.stats_service import stats_overview

router = APIRouter(prefix="/stats", tags=["stats"])


class CategoryCount(BaseModel):
    category: str
    count: int


class SyncRunSummary(BaseModel):
    sync_run_id: str
    started_at: str | None = None
    completed_at: str | None = None
    status: str | None = None
    scanned_count: int | None = None
    changed_count: int | None = None
    unchanged_count: int | None = None
    failed_count: int | None = None


class StatsOverview(BaseModel):
    documents_total: int
    documents_enabled: int
    chunks_total: int
    conversations_total: int
    qdrant_points: int | None = None
    by_category: list[CategoryCount] = []
    last_sync: SyncRunSummary | None = None


@router.get("/overview", response_model=StatsOverview, summary="Dashboard corpus + index stats")
def overview() -> StatsOverview:
    return StatsOverview(**stats_overview())
