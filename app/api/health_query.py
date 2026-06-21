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

def _qdrant_ok() -> bool:
    try:
        from app.indexing.vector_store import get_qdrant_client
        get_qdrant_client().get_collections()
        return True
    except Exception:
        return False

@router.get("")
def healthcheck():
    qdrant_ok = _qdrant_ok()
    uses_ollama = settings.embedding_backend == "ollama" or not settings.llm_model.startswith("claude")
    ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version") if uses_ollama else None
    healthy = qdrant_ok and (ollama_ok is not False)
    return {
        "status": "ok" if healthy else "degraded",
        "qdrant": qdrant_ok,
        "ollama": ollama_ok,
    }
