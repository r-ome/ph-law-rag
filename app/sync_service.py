from datetime import datetime, timezone
from uuid import uuid4

from app.config import load_allowed_sources
from app.db import get_connection
from app.ingestion.sync import ingest_source
from app.source_metadata import build_source_metadata
from app.observability.logger import get_logger

logger = get_logger(__name__)


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
		logger.info("sync_source_unchanged", source_id=source_id)
		counts["unchanged"] += 1
		return
	if action == "meta":
		print(f"[META] {source_id} — refreshed {n} chunks")
		logger.info("sync_source_metadata_refreshed", source_id=source_id, chunks=n)
		counts["refreshed"] += 1
		return
	if action == "reindex":
		print(f"[REINDEX] {source_id} — metadata, no new version ({n} chunks)")
		logger.info("sync_source_metadata_reindexed", source_id=source_id, chunks=n)
		counts["reindexed_meta"] += 1
		return
	raise RuntimeError(f"{source_id}: unknown metadata reconcile action {action!r}")


def _validate_reconcile_action(source_id: str, action: str) -> None:
	if action not in {"skip", "meta", "reindex"}:
		raise RuntimeError(f"{source_id}: unknown metadata reconcile action {action!r}")


def _create_sync_run_if_absent(sync_run_id: str, started_at: str) -> None:
	"""Insert a 'running' row if one doesn't already exist for this id."""
	conn = get_connection()
	try:
		conn.execute(
			"""
			INSERT OR IGNORE INTO sync_runs(
				sync_run_id, started_at, completed_at, status,
				scanned_count, changed_count, unchanged_count, failed_count
			) VALUES (?, ?, NULL, 'running', 0, 0, 0, 0);
			""",
			[sync_run_id, started_at],
		)
		conn.commit()
	finally:
		conn.close()


def _finalize_status(counts: dict, crashed: bool) -> str:
	if crashed:
		return "failed"
	succeeded = counts["changed"] + counts["unchanged"] + counts["refreshed"] + counts["reindexed_meta"]
	if counts["failed"] and succeeded == 0:
		return "failed"
	if counts["failed"]:
		return "partial"
	return "completed"


def _finalize_sync_run(sync_run_id: str, counts: dict, status: str) -> None:
	conn = get_connection()
	try:
		conn.execute(
			"""
			UPDATE sync_runs SET
				completed_at = ?, status = ?,
				scanned_count = ?, changed_count = ?,
				unchanged_count = ?, failed_count = ?
			WHERE sync_run_id = ?;
			""",
			[
				datetime.now(timezone.utc).isoformat(),
				status,
				counts["scanned"], counts["changed"],
				counts["unchanged"] + counts["refreshed"] + counts["reindexed_meta"],
				counts["failed"], sync_run_id,
			],
		)
		conn.commit()
	finally:
		conn.close()


def run_sync(sync_run_id: str | None = None) -> dict:
	sync_run_id = sync_run_id or str(uuid4())
	counts = _empty_counts()
	started_at = datetime.now(timezone.utc).isoformat()

	_create_sync_run_if_absent(sync_run_id, started_at)
	logger.info("sync_started", sync_run_id=sync_run_id)

	crashed = False
	try:
		sources = load_allowed_sources()
		for source in sources:
			counts["scanned"] += 1
			conn = get_connection()
			try:
				result = ingest_source(conn, source)
				if result.status == "failed":
					conn.rollback()
					print(f"[FAIL] {source.source_id} — {result.error}")
					logger.warning("sync_source_failed", source_id=source.source_id, error=result.error)
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
					logger.info("sync_source_indexed", source_id=source.source_id, status=result.status, chunks=chunk_count)
					conn.commit()
					print(f"[OK] {source.source_id} — {result.status}")
					logger.info("sync_source_ok", source_id=source.source_id, status=result.status)
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
				logger.warning("sync_source_failed", source_id=source.source_id, error=str(e), exc_info=True)
				counts["failed"] += 1
			finally:
				conn.close()
	except Exception as e:
		crashed = True
		logger.warning("sync_crashed", sync_run_id=sync_run_id, error=str(e), exc_info=True)
	finally:
		status = _finalize_status(counts, crashed)
		_finalize_sync_run(sync_run_id, counts, status)
		logger.info("sync_completed", sync_run_id=sync_run_id, status=status, **counts)

	return {**counts, "sync_run_id": sync_run_id, "status": status}
