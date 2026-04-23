from fastapi import FastAPI, APIRouter
from app.api.routes_query import router as query_router 
from app.api.health_query import router as health_router

app = FastAPI()

app.include_router(query_router)
app.include_router(health_router)

