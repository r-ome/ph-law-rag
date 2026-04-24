import json
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings, SourceConfig
from app.ingestion.hashing import hash_content

def save_raw_fetch(source_id:str, file_format: str, content:bytes) -> str:
	raw_dir = Path(settings.raw_data_dir)
	raw_dir.mkdir(parents=True, exist_ok=True)
	file_path = raw_dir / f"{source_id}.{file_format}"
	file_path.write_bytes(content)
	return str(file_path)

def save_normalized_document(url: str, content: str) -> str:
	normalized_dir = Path(settings.normalized_data_dir)
	normalized_dir.mkdir(parents=True, exist_ok=True)

	url_hash = hash_content(url)[:12]
	content_hash = hash_content(content)[:12]

	file_path = normalized_dir / f"{url_hash}_{content_hash}.txt"
	file_path.write_text(content, encoding="utf-8")
	return str(file_path)

def find_or_create_document(conn, source: SourceConfig) -> tuple[str, bool]:
	row = conn.execute("SELECT doc_id FROM documents WHERE url = ?;",[source.url]).fetchone()

	if row:
		return row["doc_id"], False

	doc_id = str(uuid4())
	now = datetime.now(timezone.utc).isoformat()

	conn.execute(
	"""
		INSERT INTO documents(
			doc_id,
			source_id,
			title,
			url,
			doc_type,
			file_format,
			category,
			tags_json,
			enabled,
			created_at,
			updated_at
		)
		VALUES (?,?,?,?,?,?,?,?,?,?,?);
	""", [
		doc_id,
		source.source_id,
		source.title,
		source.url,
		source.doc_type,
		source.file_format,
		source.category,
		json.dumps(source.tags),
		source.enabled,
		now,
		now	
		]
	)

	return doc_id, True

def get_latest_content_hash(conn, doc_id:str) -> str | None:
	row = conn.execute(
	"""
		SELECT content_hash
		FROM document_versions
		WHERE doc_id = ?
		ORDER BY fetched_at DESC
		LIMIT 1;
	""",
	[doc_id]
	).fetchone()

	if row:
		return row["content_hash"] if row else None

def insert_version(
	conn,
	doc_id: str,
	http_status: int,
	content_hash: str,
	content_length: int,
	raw_path: str,
	normalized_path: str,
	extraction_method: str,
	status: str
):
	version_id = str(uuid4())
	conn.execute(
	"""
		INSERT INTO document_versions(
			version_id,
			doc_id,
			fetched_at,
			http_status,
			content_hash,
			content_length,
			raw_path,
			normalized_path,
			extraction_method,
			changed_from_previous
		) VALUES (?,?,?,?,?,?,?,?,?,?);
	""",
	[
		version_id,
		doc_id,
		datetime.now(timezone.utc).isoformat(),
		http_status,
		content_hash,
		content_length,
		raw_path,
		normalized_path,
		extraction_method,
		status != "new"
	]
	)
	return version_id
