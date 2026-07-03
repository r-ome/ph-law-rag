from dataclasses import dataclass
from typing import Literal

from app.config import SourceConfig
from app.ingestion.fetcher import fetch_source
from app.ingestion.storage import (
	save_raw_fetch, save_normalized_document,
	find_or_create_document, get_latest_content_hash,
	get_latest_version_id, insert_version,
)
from app.ingestion.parser import parse_pdf, parse_html
from app.ingestion.normalizer import normalize_text
from app.ingestion.hashing import hash_content


IngestionStatus = Literal["new", "changed", "unchanged", "failed"]


@dataclass(frozen=True)
class IngestionResult:
	source_id: str
	url: str
	status: IngestionStatus
	doc_id: str | None = None
	version_id: str | None = None
	normalized_text: str | None = None
	error: str | None = None


def ingest_source(conn, source: SourceConfig) -> IngestionResult:
	fetch_result = fetch_source(source)

	if fetch_result.status == "failed":
		return IngestionResult(
			source_id=source.source_id,
			url=fetch_result.url,
			status="failed",
			error=fetch_result.error or "fetch failed",
		)

	url = fetch_result.url
	content = fetch_result.content
	if not content:
		return IngestionResult(
			source_id=source.source_id,
			url=url,
			status="failed",
			error="empty response content",
		)

	if source.file_format == "pdf":
		raw_text = parse_pdf(content)
		extraction_method = "pdfplumber"
	else:
		raw_text = parse_html(content, url, extractor=source.extractor)
		extraction_method = "bs4" if source.extractor == "bs4" else "trafilatura"

	normalized_text = normalize_text(raw_text)
	content_hash = hash_content(normalized_text)

	doc_id, is_new = find_or_create_document(conn, source)
	prev_hash = get_latest_content_hash(conn, doc_id)

	if prev_hash == content_hash:
		return IngestionResult(
			source_id=source.source_id,
			url=url,
			status="unchanged",
			doc_id=doc_id,
			version_id=get_latest_version_id(conn, doc_id),
			normalized_text=normalized_text,
		)

	raw_path = save_raw_fetch(source.source_id, source.file_format, content)
	normalized_path = save_normalized_document(source.source_id, normalized_text)
	status = "new" if is_new else "changed"
	version_id = insert_version(
		conn, doc_id, fetch_result.http_status, content_hash,
		len(normalized_text), raw_path, normalized_path,
		extraction_method, status,
	)

	return IngestionResult(
		source_id=source.source_id,
		url=url,
		status=status,
		doc_id=doc_id,
		version_id=version_id,
		normalized_text=normalized_text,
	)
