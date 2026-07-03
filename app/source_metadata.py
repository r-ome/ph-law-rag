from app.config import SourceConfig


NON_OPERATIVE_STATUSES = {"superseded", "repealed", "not_yet_effective"}


def operability_action_for(status: str | None) -> str:
	return "hide" if status in NON_OPERATIVE_STATUSES else "show"


def build_source_metadata(source: SourceConfig, doc_id: str) -> dict:
	meta = {
		"doc_id": doc_id,
		"source_id": source.source_id,
		"title": source.title,
		"official_number": source.official_number,
		"url": source.url,
		"doc_type": source.doc_type,
		"category": source.category,
		"tags": source.tags,
		"structure": source.structure,
		"status": source.status,
		"operability_action": operability_action_for(source.status),
	}
	if source.amends:
		meta["amends"] = source.amends
	if source.amends_namespace:
		meta["amends_namespace"] = source.amends_namespace
	return meta
