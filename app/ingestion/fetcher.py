import httpx
from dataclasses import dataclass
from app.config import settings, SourceConfig

@dataclass
class FetchResult:
	source_id: str
	url: str
	file_format: str
	status: str # "ok" | "failed"
	http_status: int | None
	content: bytes | None
	error: str | None

def fetch_source(source: SourceConfig) -> FetchResult:
	url = str(source.url)
	try:
		response = httpx.get(
			url,
			timeout=settings.request_timeout,
			headers={"User-Agent": "ph-law-rag/1.0"}
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
			http_status=None,
			content=None,
			error=str(e)
		)
