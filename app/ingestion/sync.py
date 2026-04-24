from uuid import uuid4
from datetime import datetime, timezone
from app.config import SourceConfig, load_allowed_sources
from app.db import get_connection
from app.ingestion.fetcher import fetch_source
from app.ingestion.storage import (
	save_raw_fetch, save_normalized_document,
	find_or_create_document, get_latest_content_hash,
	insert_version
)
from app.ingestion.parser import parse_pdf, parse_html
from app.ingestion.normalizer import normalize_text
from app.ingestion.hashing import hash_content

def process_source(source: SourceConfig):
	from app.indexing.index_service import index_document
	fetch_result = fetch_source(source)

	if fetch_result.status == "failed":
		print(f"[FAIL] {source.source_id} — {fetch_result.error}")
		return {"url": fetch_result.url, "status": "failed"}

	url = fetch_result.url
	content = fetch_result.content
 
	if content is None:
		print(f"[FAIL] {source.source_id} - empty response content")
		return { "url": url, "status": "failed" }

	if source.file_format == "pdf":
		raw_text = parse_pdf(content)
		extraction_method = "pdfplumber"
	else:
		raw_text = parse_html(content, url)
		extraction_method = "trafilatura"

	normalized_text = normalize_text(raw_text)
	content_hash = hash_content(normalized_text)

	conn = get_connection()
	try:
		doc_id, is_new = find_or_create_document(conn, source)
		prev_hash = get_latest_content_hash(conn, doc_id)

		if prev_hash == content_hash:
			print(f"[SKIP] {source.source_id} — unchanged")
			return {"url": url, "status": "unchanged"}

		raw_path = save_raw_fetch(source.source_id, source.file_format, content)
		normalized_path = save_normalized_document(source.source_id, normalized_text)
		status = "new" if is_new else "changed"
		version_id = insert_version(
			conn, doc_id, fetch_result.http_status, content_hash,
			len(normalized_text), raw_path, normalized_path,
			extraction_method,status
		)
  
		chunk_count = index_document(
			conn=conn,
			doc_id=doc_id,
			text=normalized_text,
			source_metadata={
				"doc_id": doc_id,
				"source_id": source.source_id,
				"title": source.title,
				"url": source.url,
				"doc_type": source.doc_type,
				"category": source.category,
				"tags": source.tags
			},
			version_id=version_id
		)
		print(f" indexed: {chunk_count} chunks")
		conn.commit()
		print(f"[OK] {source.source_id} — {status}")
	finally:
		conn.close()

	return {"url": url, "status": status}

def run_sync() -> dict:
	sources = load_allowed_sources()
	counts = {"scanned": 0, "changed": 0, "unchanged": 0, "failed": 0}
	sync_run_id = str(uuid4())
	started_at = datetime.now(timezone.utc).isoformat()

	for source in sources:
		counts["scanned"] += 1
		result = process_source(source)
		if result["status"] == "failed":
			counts["failed"] += 1
		elif result["status"] == "unchanged":
			counts["unchanged"] += 1
		else:
			counts["changed"] += 1

	conn = get_connection()
	try:
		conn.execute(
			"""
			INSERT INTO sync_runs(
				sync_run_id,
				started_at,
				completed_at,
				status,
				scanned_count,
				changed_count,
				unchanged_count,
				failed_count
			) VALUES (?,?,?,?,?,?,?,?);
			""",
			[
				sync_run_id,
				started_at,
				datetime.now(timezone.utc).isoformat(),
				"completed",
				counts["scanned"],
				counts["changed"],
				counts["unchanged"],
				counts["failed"]
			]
		)
		conn.commit()
	finally:
		conn.close()

	return counts
