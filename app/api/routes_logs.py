from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.log_reader import read_logs

router = APIRouter(prefix="/logs", tags=["logs"])


class LogEntry(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    event: str | None = None
    logger: str | None = None
    raw: str | None = None
    extra: dict[str, Any] | None = None


class LogResponse(BaseModel):
    entries: list[LogEntry]
    count: int


@router.get("", response_model=LogResponse, summary="Tail of the app log")
def logs(lines: int = 200, level: str | None = None) -> LogResponse:
    entries = read_logs(lines=lines, level=level)
    return LogResponse(entries=entries, count=len(entries))
