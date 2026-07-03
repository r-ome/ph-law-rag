import httpx


def ping_url(url: str) -> bool:
	try:
		response = httpx.get(url, timeout=2.0)
		return response.is_success
	except httpx.HTTPError:
		return False


def qdrant_ok() -> bool:
	try:
		from app.indexing.vector_store import get_qdrant_client
		get_qdrant_client().get_collections()
		return True
	except Exception:
		return False
