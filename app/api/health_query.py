import httpx
from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])

def ping_url(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=2.0)
        return response.is_success
    except httpx.HTTPError:
        return False

@router.get("")
def healthcheck():
    qdrant_ok = ping_url(f"{settings.qdrant_url}/collections")
    ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version")
    return {
        "status": "ok" if qdrant_ok and ollama_ok else "degraded",
        "qdrant": qdrant_ok,
        "ollama": ollama_ok,
    }
