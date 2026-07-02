import ssl
import httpx
from dataclasses import dataclass
from app.config import settings, SourceConfig


def _ssl_verify() -> ssl.SSLContext | bool:
	"""OS trust store when available: government sites (elibrary.judiciary.gov.ph) serve
	incomplete cert chains that certifi can't build but the OS resolves via AIA fetching."""
	try:
		import truststore
		return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
	except ImportError:
		return True

@dataclass
class FetchResult:
	source_id: str
	url: str
	file_format: str
	status: str # "ok" | "failed"
	http_status: int
	content: bytes | None
	error: str | None

def fetch_source(source: SourceConfig) -> FetchResult:
	url = str(source.url)
	try:
		response = httpx.get(
			url,
			timeout=settings.request_timeout,
			headers={"User-Agent": "ph-law-rag/1.0"},
			verify=_ssl_verify(),
		)
		response.raise_for_status()
		return FetchResult(
			source_id=source.source_id,
			url=url,
			file_format=source.file_format,
			status="ok",
			http_status=response.status_code,
			content=response.content,
			error=None
		)
	except Exception as e:
		return FetchResult(
			source_id=source.source_id,
			url=url,
			file_format=source.file_format,
			status="failed",
			http_status=500,
			content=None,
			error=str(e)
		)
