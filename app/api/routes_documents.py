from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.corpus_service import get_document_detail, list_documents_enriched
from app.db import list_chunks
from app.sync_service import _create_sync_run_if_absent, run_sync

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentSummary(BaseModel):
    doc_id: str
    source_id: str
    title: str
    url: str
    doc_type: str
    category: str
    enabled: bool
    updated_at: str | None = None
    last_fetched: str | None = None
    chunk_count: int
    status: str = "unknown"
    source_index: str | None = None
    official_number: str | None = None
    tags: list[str] = []


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class DocumentDetail(DocumentSummary):
    normalized_text: str
    content_hash: str | None = None
    content_length: int | None = None
    extraction_method: str | None = None
    http_status: int | None = None
    approval_date: str | None = None
    effectivity_date: str | None = None
    availability: str | None = None
    structure: str | None = None
    notes: str | None = None
    amends: list[str] = []
    repeals: list[str] = []
    supersedes: list[str] = []
    implements: list[str] = []
    amends_namespace: str | None = None


class ChunkSummary(BaseModel):
    chunk_id: str
    chunk_index: int | None = None
    text: str
    char_count: int
    token_estimate: int
    qdrant_id: str | None = None


class ChunkListResponse(BaseModel):
    doc_id: str
    chunk_count: int
    chunks: list[ChunkSummary]


class SyncStartedResponse(BaseModel):
    status: str
    sync_run_id: str


@router.get("", response_model=DocumentListResponse, summary="List all documents")
def documents() -> DocumentListResponse:
    return DocumentListResponse(documents=list_documents_enriched())


@router.get(
    "/{doc_id}",
    response_model=DocumentDetail,
    summary="Document metadata + normalized text",
)
def document_detail(doc_id: str) -> DocumentDetail:
    detail = get_document_detail(doc_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentDetail(**detail)


@router.get(
    "/{doc_id}/chunks",
    response_model=ChunkListResponse,
    summary="Chunks for a document",
)
def document_chunks(doc_id: str) -> ChunkListResponse:
    if get_document_detail(doc_id) is None:
        raise HTTPException(status_code=404, detail="document not found")
    chunks = list_chunks(doc_id)
    return ChunkListResponse(doc_id=doc_id, chunk_count=len(chunks), chunks=chunks)


@router.post("/sync", response_model=SyncStartedResponse, summary="Trigger a background sync")
def sync(background_tasks: BackgroundTasks) -> SyncStartedResponse:
    sync_run_id = str(uuid.uuid4())
    _create_sync_run_if_absent(sync_run_id, datetime.now(timezone.utc).isoformat())
    background_tasks.add_task(run_sync, sync_run_id)
    return SyncStartedResponse(status="sync started", sync_run_id=sync_run_id)
