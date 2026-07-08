import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import SourceConfig, load_allowed_sources
from app.indexing.amendment_timeline import TimelineEntry, build_timelines
from app.indexing.chunker import ProvisionSpan, provision_spans
from app.indexing.provision_status import load_provision_overrides

MIN_LENGTH_RATIO = 0.7
MAX_LENGTH_RATIO = 1.5
QUOTE_CHARS = "\"“”‘’'"


@dataclass(frozen=True)
class Splice:
	key: str
	base_source_id: str
	amendment_source_id: str
	amendment_official_number: str
	amendment_approval_date: str
	length_ratio: float | None = None
	amendment_provision_id: str | None = None
	unit_type: str | None = None
	unit_number: str | None = None


@dataclass(frozen=True)
class SplicePlan:
	splices_by_base_doc: dict[str, tuple[Splice, ...]]
	hidden_keys_by_amendment: dict[str, tuple[str, ...]]
	exclusions: tuple[dict, ...]
	preflight_mismatches: tuple[dict, ...]


def build_splice_plan(conn) -> SplicePlan:
	sources = {source.source_id: source for source in load_allowed_sources()}
	latest = _latest_versions(conn)
	overrides = load_provision_overrides()
	timelines = build_timelines(conn)

	splices_by_base: dict[str, list[Splice]] = {}
	hidden_by_amendment: dict[str, list[str]] = {}
	exclusions: list[dict] = []
	preflight_mismatches: list[dict] = []

	for key, timeline in sorted(timelines.timelines.items()):
		base_entries = [entry for entry in timeline.entries if not entry.is_insertion]
		insertion_entries = [entry for entry in timeline.entries if entry.is_insertion]

		if not insertion_entries:
			continue
		if not base_entries:
			exclusions.append(_exclusion(key, "no_base", _entry_detail(insertion_entries)))
			continue
		if len(insertion_entries) != 1:
			exclusions.append(_exclusion(key, "chain", _entry_detail(insertion_entries)))
			continue

		insertion = insertion_entries[0]
		base = base_entries[0]
		if insertion.provision_partial:
			exclusions.append(_exclusion(key, "partial", _entry_detail([insertion])))
			continue
		if key in overrides:
			exclusions.append(_exclusion(key, "override_collision", _entry_detail([insertion])))
			continue
		if insertion.length_ratio is None or not (
			MIN_LENGTH_RATIO <= insertion.length_ratio <= MAX_LENGTH_RATIO
		):
			exclusions.append(
				_exclusion(
					key,
					"ratio_outlier",
					{
						**_entry_detail([insertion]),
						"length_ratio": insertion.length_ratio,
					},
				)
			)
			continue

		preflight = _preflight_partial(
			key=key,
			insertion=insertion,
			sources=sources,
			latest=latest,
		)
		if preflight is not None:
			preflight_mismatches.append(preflight)
			exclusions.append(_exclusion(key, "preflight_mismatch", preflight))
			continue

		source = sources.get(insertion.source_id)
		if source is None:
			exclusions.append(_exclusion(key, "missing_source", {"source_id": insertion.source_id}))
			continue

		splice = Splice(
			key=key,
			base_source_id=base.source_id,
			amendment_source_id=insertion.source_id,
			amendment_official_number=source.official_number or insertion.source_id,
			amendment_approval_date=insertion.approval_date,
			length_ratio=insertion.length_ratio,
			amendment_provision_id=insertion.provision_id,
			unit_type=_first_or_none(_unit_types(insertion)),
			unit_number=_first_or_none(_unit_numbers(insertion)),
		)
		splices_by_base.setdefault(base.source_id, []).append(splice)
		hidden_by_amendment.setdefault(insertion.source_id, []).append(key)
		if insertion.provision_id != key:
			hidden_by_amendment.setdefault(insertion.source_id, []).append(insertion.provision_id)

	return SplicePlan(
		splices_by_base_doc={
			source_id: tuple(sorted(splices, key=lambda splice: splice.key))
			for source_id, splices in sorted(splices_by_base.items())
		},
		hidden_keys_by_amendment={
			source_id: tuple(sorted(keys))
			for source_id, keys in sorted(hidden_by_amendment.items())
		},
		exclusions=tuple(exclusions),
		preflight_mismatches=tuple(preflight_mismatches),
	)


def consolidate(
	base_text: str,
	base_spans: list[ProvisionSpan],
	splices: tuple[Splice, ...],
	amendment_texts: dict[str, str],
) -> str:
	replacements: list[tuple[int, int, str, Splice]] = []
	for splice in splices:
		base_span = _find_span(base_spans, splice.key, splice)
		if base_span is None:
			raise ValueError(f"{splice.key}: base provision span not found")

		amendment_text = amendment_texts.get(splice.amendment_source_id)
		if amendment_text is None:
			raise ValueError(f"{splice.key}: amendment text not loaded for {splice.amendment_source_id}")
		amendment_meta = _span_metadata(
			source_id=splice.amendment_source_id,
			amends=[splice.base_source_id],
			amends_namespace=splice.base_source_id,
		)
		amendment_span = _find_span(
			provision_spans(amendment_text, amendment_meta),
			splice.amendment_provision_id or splice.key,
			splice,
			inserted_only=True,
		)
		if amendment_span is None:
			raise ValueError(f"{splice.key}: amendment restatement span not found")

		replacement = _dequote_unit(amendment_text[amendment_span.start:amendment_span.end]).strip()
		replacement = (
			f"{replacement} [as amended by {splice.amendment_official_number}, "
			f"approved {splice.amendment_approval_date}]"
		)
		if base_text[base_span.start:base_span.end].endswith("\n"):
			replacement += "\n"
		replacements.append((base_span.start, base_span.end, replacement, splice))

	spliced = base_text
	for start, end, replacement, _ in sorted(replacements, key=lambda item: item[0], reverse=True):
		spliced = spliced[:start] + replacement + spliced[end:]

	for splice in splices:
		meta = _span_metadata(source_id=splice.base_source_id)
		if _find_span(provision_spans(spliced, meta), splice.key, splice) is None:
			raise ValueError(f"{splice.key}: post-splice structural parse lost the provision")
	return spliced


def load_amendment_texts(conn, splices: tuple[Splice, ...]) -> dict[str, str]:
	latest = _latest_versions(conn)
	texts: dict[str, str] = {}
	for source_id in sorted({splice.amendment_source_id for splice in splices}):
		info = latest.get(source_id)
		if info is None:
			raise ValueError(f"{source_id}: no latest document version for consolidation")
		path = Path(info["normalized_path"])
		if not path.exists():
			raise ValueError(f"{source_id}: normalized file missing: {path}")
		texts[source_id] = path.read_text()
	return texts


def plan_report(plan: SplicePlan) -> dict:
	splices = [
		{
			"key": splice.key,
			"base": splice.base_source_id,
			"amendment": splice.amendment_source_id,
			"ratio": splice.length_ratio,
		}
		for source_id in sorted(plan.splices_by_base_doc)
		for splice in plan.splices_by_base_doc[source_id]
	]
	return {
		"summary": {
			"splices": len(splices),
			"exclusions": len(plan.exclusions),
			"preflight_mismatches": len(plan.preflight_mismatches),
		},
		"splices": splices,
		"exclusions": list(plan.exclusions),
		"preflight_mismatches": list(plan.preflight_mismatches),
	}


def check_consolidation_coherence(conn, plan: SplicePlan) -> None:
	errors: list[str] = []
	for source_id, splices in plan.splices_by_base_doc.items():
		for splice in splices:
			base_rows = _metadata_rows(
				conn,
				"""
				SELECT metadata_json FROM chunks
				WHERE json_extract(metadata_json, '$.source_id') = ?
				  AND json_extract(metadata_json, '$.provision_id') = ?
				""",
				[source_id, splice.key],
			)
			if not base_rows or not all(row.get("consolidated") == 1 for row in base_rows):
				errors.append(f"{splice.key}: base chunks not consolidated in {source_id}")

			hidden_rows = _metadata_rows(
				conn,
				"""
				SELECT metadata_json FROM chunks
				WHERE json_extract(metadata_json, '$.source_id') = ?
				  AND (
					json_extract(metadata_json, '$.provision_id') = ?
					OR json_extract(metadata_json, '$.provision_id') = ?
				  )
				  AND json_extract(metadata_json, '$.inserted_into') IS NOT NULL
				""",
				[
					splice.amendment_source_id,
					splice.key,
					splice.amendment_provision_id or splice.key,
				],
			)
			if not hidden_rows or not all(
				row.get("operability_action") == "hide"
				and row.get("provision_status") == "consolidated"
				and row.get("operability_basis") == "consolidated"
				for row in hidden_rows
			):
				errors.append(f"{splice.key}: amendment chunks not hidden in {splice.amendment_source_id}")
	if errors:
		raise RuntimeError("consolidation coherence failed: " + "; ".join(errors))


def _metadata_rows(conn, sql: str, params: list[Any]) -> list[dict]:
	rows = conn.execute(sql, params).fetchall()
	out: list[dict] = []
	for row in rows:
		try:
			out.append(json.loads(row["metadata_json"] or "{}"))
		except json.JSONDecodeError:
			continue
	return out


def _latest_versions(conn) -> dict[str, dict]:
	rows = conn.execute(
		"""
		SELECT d.source_id, d.doc_id, v.normalized_path
		FROM documents d
		JOIN document_versions v ON v.doc_id = d.doc_id
		JOIN (
			SELECT doc_id, MAX(fetched_at) AS fetched_at
			FROM document_versions
			GROUP BY doc_id
		) latest ON latest.doc_id = v.doc_id AND latest.fetched_at = v.fetched_at
		"""
	).fetchall()
	return {row["source_id"]: dict(row) for row in rows}


def _preflight_partial(
	*,
	key: str,
	insertion: TimelineEntry,
	sources: dict[str, SourceConfig],
	latest: dict[str, dict],
) -> dict | None:
	source = sources.get(insertion.source_id)
	info = latest.get(insertion.source_id)
	if source is None or info is None:
		return {
			"key": key,
			"source_id": insertion.source_id,
			"stored_partial": insertion.provision_partial,
			"recomputed_partial": None,
			"error": "missing_source_or_version",
		}
	path = Path(info["normalized_path"])
	if not path.exists():
		return {
			"key": key,
			"source_id": insertion.source_id,
			"stored_partial": insertion.provision_partial,
			"recomputed_partial": None,
			"error": f"missing_normalized_file:{path}",
		}
	text = path.read_text()
	spans = provision_spans(text, _source_metadata(source, info["doc_id"]))
	span = _find_span(
		spans,
		insertion.provision_id,
		Splice(
			key=key,
			base_source_id=key.split(":", 1)[0],
			amendment_source_id=insertion.source_id,
			amendment_official_number="",
			amendment_approval_date=insertion.approval_date,
			amendment_provision_id=insertion.provision_id,
			unit_type=_first_or_none(_unit_types(insertion)),
			unit_number=_first_or_none(_unit_numbers(insertion)),
		),
		inserted_only=True,
	)
	if span is None:
		return {
			"key": key,
			"source_id": insertion.source_id,
			"stored_partial": insertion.provision_partial,
			"recomputed_partial": None,
			"error": "span_not_found",
		}
	if span.partial != insertion.provision_partial:
		return {
			"key": key,
			"source_id": insertion.source_id,
			"stored_partial": insertion.provision_partial,
			"recomputed_partial": span.partial,
		}
	return None


def _find_span(
	spans: list[ProvisionSpan],
	provision_id: str,
	splice: Splice,
	inserted_only: bool = False,
) -> ProvisionSpan | None:
	candidates = [span for span in spans if not inserted_only or span.inserted]
	for span in candidates:
		if span.provision_id == provision_id:
			return span
	if splice.unit_type and splice.unit_number:
		matches = [
			span for span in candidates
			if span.unit_type == splice.unit_type and span.unit_number == splice.unit_number
		]
		if len(matches) == 1:
			return matches[0]
	return None


def _dequote_unit(text: str) -> str:
	lines = [
		re.sub(rf"^(\s*)[{re.escape(QUOTE_CHARS)}]", r"\1", line, count=1)
		for line in text.splitlines()
	]
	out = "\n".join(lines)
	out = re.sub(rf"[{re.escape(QUOTE_CHARS)}]\.\s*$", ".", out)
	out = re.sub(rf"[{re.escape(QUOTE_CHARS)}]\s*$", "", out)
	return out


def _source_metadata(source: SourceConfig, doc_id: str) -> dict:
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
	}
	if source.amends:
		meta["amends"] = source.amends
	if source.amends_namespace:
		meta["amends_namespace"] = source.amends_namespace
	return meta


def _span_metadata(
	source_id: str,
	amends: list[str] | None = None,
	amends_namespace: str | None = None,
) -> dict:
	meta = {"source_id": source_id, "structure": "hierarchical"}
	if amends:
		meta["amends"] = amends
	if amends_namespace:
		meta["amends_namespace"] = amends_namespace
	return meta


def _exclusion(key: str, reason: str, detail: dict) -> dict:
	return {"key": key, "reason": reason, "detail": detail}


def _entry_detail(entries: list[TimelineEntry]) -> dict:
	return {
		"entries": [
			{
				"source_id": entry.source_id,
				"provision_id": entry.provision_id,
				"approval_date": entry.approval_date,
				"partial": entry.provision_partial,
				"length_ratio": entry.length_ratio,
			}
			for entry in entries
		]
	}


def _unit_types(entry: TimelineEntry) -> list[str]:
	return [
		label.split(" ", 1)[0].lower()
		for label in entry.unit_labels
		if " " in label
	]


def _unit_numbers(entry: TimelineEntry) -> list[str]:
	return [
		label.split(" ", 1)[1]
		for label in entry.unit_labels
		if " " in label
	]


def _first_or_none(values: list[str]) -> str | None:
	return values[0] if values else None
