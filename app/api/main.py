from fastapi import FastAPI, APIRouter
from app.observability.logger import configure_logging

configure_logging()

from app.api.routes_query import router as query_router 
from app.api.health_query import router as health_router
from app.api.routes_documents import router as documents_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_stats import router as stats_router
from app.api.routes_sync import router as sync_router
from app.api.routes_config import router as config_router
from app.api.routes_retrieval import router as retrieval_router
from app.api.routes_traces import router as traces_router
from app.api.routes_logs import router as logs_router
from app.api.routes_evals import router as evals_router
from app.api.routes_chunks import router as chunks_router

app = FastAPI(title="PH Law RAG API", version="0.1.0")

app.include_router(query_router)
app.include_router(health_router)
app.include_router(documents_router)
app.include_router(conversations_router)
app.include_router(stats_router)
app.include_router(sync_router)
app.include_router(config_router)
app.include_router(retrieval_router)
app.include_router(traces_router)
app.include_router(logs_router)
app.include_router(evals_router)
app.include_router(chunks_router)
