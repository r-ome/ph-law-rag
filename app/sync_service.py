from datetime import datetime, timezone
from uuid import uuid4

from app.config import load_allowed_sources
from app.db import get_connection
from app.ingestion.sync import ingest_source
from app.source_metadata import build_source_metadata


def _empty_counts() -> dict:
	return {
		"scanned": 0,
		"changed": 0,
		"unchanged": 0,
		"failed": 0,
		"refreshed": 0,
		"reindexed_meta": 0,
	}


def _require_result_fields(result) -> None:
	if result.doc_id is None:
		raise RuntimeError(f"{result.source_id}: ingestion result missing doc_id")
	if result.version_id is None:
		raise RuntimeError(f"{result.source_id}: ingestion result missing version_id")
	if result.normalized_text is None:
		raise RuntimeError(f"{result.source_id}: ingestion result missing normalized_text")


def _record_reconcile(source_id: str, action: str, n: int, counts: dict) -> None:
	if action == "skip":
		print(f"[SKIP] {source_id} — unchanged")
		counts["unchanged"] += 1
		return
	if action == "meta":
		print(f"[META] {source_id} — refreshed {n} chunks")
		counts["refreshed"] += 1
		return
	if action == "reindex":
		print(f"[REINDEX] {source_id} — metadata, no new version ({n} chunks)")
		counts["reindexed_meta"] += 1
		return
	raise RuntimeError(f"{source_id}: unknown metadata reconcile action {action!r}")


def _validate_reconcile_action(source_id: str, action: str) -> None:
	if action not in {"skip", "meta", "reindex"}:
		raise RuntimeError(f"{source_id}: unknown metadata reconcile action {action!r}")


def _write_sync_run(sync_run_id: str, started_at: str, counts: dict) -> None:
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
				counts["unchanged"] + counts["refreshed"] + counts["reindexed_meta"],
				counts["failed"],
			],
		)
		conn.commit()
	finally:
		conn.close()


def run_sync() -> dict:
	sources = load_allowed_sources()
	counts = _empty_counts()
	sync_run_id = str(uuid4())
	started_at = datetime.now(timezone.utc).isoformat()

	for source in sources:
		counts["scanned"] += 1
		conn = get_connection()
		try:
			result = ingest_source(conn, source)
			if result.status == "failed":
				conn.rollback()
				print(f"[FAIL] {source.source_id} — {result.error}")
				counts["failed"] += 1
				continue

			_require_result_fields(result)

			from app.indexing import index_service

			metadata = build_source_metadata(source, result.doc_id)
			if result.status in ("new", "changed"):
				chunk_count = index_service.index_document(
					conn=conn,
					doc_id=result.doc_id,
					text=result.normalized_text,
					source_metadata=metadata,
					version_id=result.version_id,
				)
				print(f" indexed: {chunk_count} chunks")
				conn.commit()
				print(f"[OK] {source.source_id} — {result.status}")
				counts["changed"] += 1
				continue

			action, n = index_service.refresh_document_metadata(
				conn,
				result.doc_id,
				metadata,
				result.normalized_text,
				result.version_id,
			)
			_validate_reconcile_action(source.source_id, action)
			conn.commit()
			_record_reconcile(source.source_id, action, n, counts)

		except Exception as e:
			conn.rollback()
			print(f"[FAIL] {source.source_id} — {e}")
			counts["failed"] += 1
		finally:
			conn.close()

	_write_sync_run(sync_run_id, started_at, counts)
	return counts
