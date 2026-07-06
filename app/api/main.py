from fastapi import FastAPI, APIRouter
from app.observability.logger import configure_logging

configure_logging()

from app.api.routes_query import router as query_router 
from app.api.health_query import router as health_router
from app.api.routes_documents import router as documents_router

app = FastAPI()

app.include_router(query_router)
app.include_router(health_router)
app.include_router(documents_router)
