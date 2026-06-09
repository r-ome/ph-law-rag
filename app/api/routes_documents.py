from fastapi import APIRouter, BackgroundTasks
from app.ingestion.sync import run_sync
from app.db import list_documents

router = APIRouter(prefix="/documents",tags=["documents"])

@router.get("")
def documents():
    return { "documents": list_documents() }

@router.post("/sync")
def sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sync)
    return { "status": "sync started" }
