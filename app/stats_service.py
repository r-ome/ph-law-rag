from app.config import settings
from app.db import corpus_counts, list_sync_runs


def _qdrant_point_count() -> int | None:
    """Best-effort exact count of points in the collection."""
    try:
        from app.indexing.vector_store import get_qdrant_client

        result = get_qdrant_client().count(
            collection_name=settings.qdrant_collection, exact=True
        )
        return int(result.count)
    except Exception:
        return None


def stats_overview() -> dict:
    counts = corpus_counts()
    runs = list_sync_runs(limit=1)
    return {
        **counts,
        "qdrant_points": _qdrant_point_count(),
        "last_sync": runs[0] if runs else None,
    }
