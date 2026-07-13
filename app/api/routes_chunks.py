from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_chunks_by_ids

router = APIRouter(prefix="/chunks", tags=["chunks"])


class ChunkLookupRequest(BaseModel):
    chunk_ids: list[str] = Field(default_factory=list, max_length=64)


class ChunkLookupHit(BaseModel):
    chunk_id: str
    doc_id: str
    chunk_index: int | None = None
    text: str
    char_count: int | None = None
    title: str | None = None


class ChunkLookupResponse(BaseModel):
    chunks: list[ChunkLookupHit]
    missing: list[str]


@router.post("/lookup", response_model=ChunkLookupResponse, summary="Resolve chunk IDs to chunks")
def lookup_chunks(req: ChunkLookupRequest) -> ChunkLookupResponse:
    ids = list(dict.fromkeys(req.chunk_ids))  # dedupe, preserve order
    by_id = {c["chunk_id"]: c for c in get_chunks_by_ids(ids)}
    return ChunkLookupResponse(
        chunks=[ChunkLookupHit(**by_id[i]) for i in ids if i in by_id],
        missing=[i for i in ids if i not in by_id],
    )
