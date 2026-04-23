from datetime import datetime, timezone
from app.config import settings
from pathlib import Path
import sqlite3

def get_connection():
	try:
		conn = sqlite3.connect(settings.db_path)
		conn.execute("PRAGMA foreign_keys = ON")
		conn.row_factory = sqlite3.Row
		return conn
	except sqlite3.Error as e:
		raise RuntimeError(f"Database connection failed: {e}")

MIGRATIONS = [
	(
		1,
		"create core tables",
		"""
		CREATE TABLE IF NOT EXISTS documents(
			doc_id TEXT PRIMARY KEY,
			source_id TEXT NOT NULL,
			title TEXT,
			url TEXT,
			doc_type TEXT,
			file_format TEXT,
			category TEXT,
			tags_json TEXT,
			enabled INTEGER NOT NULL,
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		);
  
		CREATE TABLE IF NOT EXISTS document_versions(
			version_id TEXT PRIMARY KEY,
			doc_id TEXT NOT NULL,
			fetched_at TEXT NOT NULL,
			http_status INTEGER NOT NULL,
			content_hash TEXT NOT NULL,
			content_length INTEGER NOT NULL,
			raw_path TEXT NOT NULL,
			normalized_path TEXT NOT NULL,
			extraction_method TEXT NOT NULL,
			changed_from_previous INTEGER,
			FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
		);
  
		CREATE TABLE IF NOT EXISTS chunks(
			chunk_id TEXT PRIMARY KEY,
			doc_id TEXT NOT NULL,
			version_id TEXT NOT NULL,
			chunk_index INTEGER,
			text TEXT NOT NULL,
			char_count INTEGER NOT NULL,
			token_estimate INTEGER NOT NULL,
			qdrant_id TEXT,
			metadata_json TEXT,
			created_at TEXT NOT NULL,
			FOREIGN KEY (doc_id) REFERENCES documents(doc_id),
			FOREIGN KEY (version_id) REFERENCES document_versions(version_id)
		);
  
		CREATE TABLE IF NOT EXISTS sync_runs(
			sync_run_id TEXT PRIMARY KEY,
			started_at TEXT,
			completed_at TEXT,
			status TEXT,
			scanned_count INTEGER,
			changed_count INTEGER,
			unchanged_count INTEGER,
			failed_count INTEGER
		);
		""",
	)

]



def _bootstrap_migrations(conn):
	conn.execute("""
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version INTEGER PRIMARY KEY,
			applied_at TEXT NOT NULL,
			description TEXT NOT NULL
		)
	""")
	conn.commit()

def _apply_migrations(conn):
	applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
	for version, description, sql in MIGRATIONS:
		if version in applied:
			continue
		conn.executescript(sql)
		conn.execute(
			"INSERT INTO schema_migrations(version, applied_at, description) VALUES (?,?,?)",
   			(version, datetime.now(timezone.utc).isoformat(), description)
		)
		conn.commit()

def init_db():
	db_path = Path(settings.db_path)
	db_path.parent.mkdir(parents=True,exist_ok=True)
	conn = get_connection()
	_bootstrap_migrations(conn)
	_apply_migrations(conn)
	conn.close()
