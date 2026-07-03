from fastapi import APIRouter
from app.config import settings
from app.runtime.health import ping_url, qdrant_ok

router = APIRouter(prefix="/health", tags=["health"])

def _qdrant_ok() -> bool:
    return qdrant_ok()

@router.get("")
def healthcheck():
    qdrant_healthy = qdrant_ok()
    uses_ollama = settings.embedding_backend == "ollama" or not settings.llm_model.startswith("claude")
    ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version") if uses_ollama else None
    healthy = qdrant_healthy and (ollama_ok is not False)
    return {
        "status": "ok" if healthy else "degraded",
        "qdrant": qdrant_healthy,
        "ollama": ollama_ok,
    }
