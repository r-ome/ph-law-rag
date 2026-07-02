import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.config import load_allowed_sources


@dataclass(frozen=True)
class TimelineEntry:
	source_id: str
	approval_date: str
	provision_id: str
	unit_labels: tuple[str, ...]
	is_insertion: bool
	provision_partial: bool
	char_count: int
	length_ratio: float | None


@dataclass(frozen=True)
class ProvisionTimeline:
	key: str
	entries: tuple[TimelineEntry, ...]


@dataclass(frozen=True)
class TimelineBuildResult:
	timelines: dict[str, ProvisionTimeline]
	ambiguous_insertions: tuple[dict, ...]
	same_date_conflicts: tuple[dict, ...]
	missing_dates: tuple[dict, ...]


@dataclass(frozen=True)
class _Chunk:
	provision_id: str
	source_id: str
	unit_type: str | None
	unit_number: str | None
	unit_label: str | None
	inserted_into: str | None
	provision_partial: bool
	chunk_index: int
	char_count: int


@dataclass(frozen=True)
class _EntryDraft:
	key: str
	source_id: str
	provision_id: str
	is_insertion: bool
	unit_labels: tuple[str, ...]
	provision_partial: bool
	char_count: int


def build_timelines(conn) -> TimelineBuildResult:
	"""Build per-provision amendment timelines from indexed chunk metadata.

	This is intentionally read-only. Ambiguous or under-specified ordering is surfaced
	as diagnostics instead of guessed.
	"""
	sources = {source.source_id: source for source in load_allowed_sources()}
	chunks = _load_chunks(conn)
	base_pids = {chunk.provision_id for chunk in chunks if not chunk.inserted_into}

	grouped: dict[tuple[str, str, bool], list[_Chunk]] = {}
	ambiguous: dict[tuple[str, str, tuple[str, ...]], dict] = {}
	for chunk in chunks:
		resolved_key, candidates = _resolve_key(chunk, base_pids)
		if candidates is not None:
			diag_key = (chunk.provision_id, chunk.source_id, tuple(candidates))
			ambiguous[diag_key] = {
				"pid": chunk.provision_id,
				"source_id": chunk.source_id,
				"candidates": candidates,
			}
			continue
		grouped.setdefault(
			(resolved_key, chunk.source_id, bool(chunk.inserted_into)), []
		).append(chunk)

	entries_by_key: dict[str, list[_EntryDraft]] = {}
	for (key, source_id, is_insertion), entry_chunks in grouped.items():
		draft = _entry_from_chunks(key, source_id, is_insertion, entry_chunks)
		entries_by_key.setdefault(key, []).append(draft)

	timelines: dict[str, ProvisionTimeline] = {}
	same_date_conflicts: list[dict] = []
	missing_dates: list[dict] = []
	for key in sorted(entries_by_key):
		drafts = entries_by_key[key]
		if len(drafts) > 1:
			missing = [
				{"key": key, "source_id": draft.source_id}
				for draft in drafts
				if _approval_date(sources, draft.source_id) is None
			]
			if missing:
				missing_dates.extend(missing)
				continue

			conflicts = _same_date_conflicts(key, drafts, sources)
			if conflicts:
				same_date_conflicts.extend(conflicts)
				continue

		ordered = _order_entries(key, drafts, sources)
		timelines[key] = ProvisionTimeline(
			key=key,
			entries=_finalize_entries(ordered, sources),
		)

	return TimelineBuildResult(
		timelines=timelines,
		ambiguous_insertions=tuple(ambiguous[k] for k in sorted(ambiguous)),
		same_date_conflicts=tuple(same_date_conflicts),
		missing_dates=tuple(missing_dates),
	)


def _load_chunks(conn) -> list[_Chunk]:
	cursor = conn.execute(
		"""
			SELECT chunk_index, char_count, metadata_json
			FROM chunks
			WHERE metadata_json IS NOT NULL
			ORDER BY chunk_index
		"""
	)
	rows = _dict_rows(cursor)
	chunks: list[_Chunk] = []
	for row in rows:
		try:
			meta = json.loads(row["metadata_json"] or "{}")
		except json.JSONDecodeError:
			continue
		pid = meta.get("provision_id")
		source_id = meta.get("source_id")
		if not pid or not source_id:
			continue
		chunks.append(
			_Chunk(
				provision_id=str(pid),
				source_id=str(source_id),
				unit_type=_str_or_none(meta.get("unit_type")),
				unit_number=_str_or_none(meta.get("unit_number")),
				unit_label=_str_or_none(meta.get("unit_label")),
				inserted_into=_str_or_none(meta.get("inserted_into")),
				provision_partial=bool(meta.get("provision_partial")),
				chunk_index=int(row["chunk_index"] or 0),
				char_count=int(row["char_count"] or 0),
			)
		)
	return chunks


def _dict_rows(cursor) -> list[dict[str, Any]]:
	names = [desc[0] for desc in cursor.description]
	return [dict(zip(names, row)) for row in cursor.fetchall()]


def _str_or_none(value: Any) -> str | None:
	if value is None:
		return None
	text = str(value)
	return text if text else None


def _resolve_key(chunk: _Chunk, base_pids: set[str]) -> tuple[str, list[str] | None]:
	if not chunk.inserted_into:
		return chunk.provision_id, None
	if chunk.provision_id in base_pids:
		return chunk.provision_id, None
	if not chunk.unit_type or not chunk.unit_number:
		return chunk.provision_id, None

	namespace = chunk.inserted_into
	suffix = f":{chunk.unit_type}:{chunk.unit_number}".lower()
	candidates = sorted(
		pid for pid in base_pids
		if pid.startswith(f"{namespace}:") and pid.endswith(suffix)
	)
	if len(candidates) == 1:
		return candidates[0], None
	if len(candidates) > 1:
		return chunk.provision_id, candidates
	return chunk.provision_id, None


def _entry_from_chunks(
	key: str,
	source_id: str,
	is_insertion: bool,
	chunks: list[_Chunk],
) -> _EntryDraft:
	ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
	labels: list[str] = []
	seen_labels: set[str] = set()
	for chunk in ordered:
		if chunk.unit_label and chunk.unit_label not in seen_labels:
			seen_labels.add(chunk.unit_label)
			labels.append(chunk.unit_label)
	return _EntryDraft(
		key=key,
		source_id=source_id,
		provision_id=ordered[0].provision_id,
		is_insertion=is_insertion,
		unit_labels=tuple(labels),
		provision_partial=any(chunk.provision_partial for chunk in ordered),
		char_count=sum(chunk.char_count for chunk in ordered),
	)


def _approval_date(sources: dict[str, Any], source_id: str) -> date | None:
	source = sources.get(source_id)
	if source is None:
		return None
	return source.approval_date


def _date_string(sources: dict[str, Any], source_id: str) -> str:
	value = _approval_date(sources, source_id)
	return value.isoformat() if value is not None else ""


def _same_date_conflicts(
	key: str,
	drafts: list[_EntryDraft],
	sources: dict[str, Any],
) -> list[dict]:
	by_date: dict[date, list[str]] = {}
	for draft in drafts:
		if not draft.is_insertion:
			continue
		value = _approval_date(sources, draft.source_id)
		if value is not None:
			by_date.setdefault(value, []).append(draft.source_id)

	return [
		{
			"key": key,
			"source_ids": tuple(sorted(source_ids)),
			"date": value.isoformat(),
		}
		for value, source_ids in sorted(by_date.items())
		if len(source_ids) > 1
	]


def _order_entries(
	key: str,
	drafts: list[_EntryDraft],
	sources: dict[str, Any],
) -> list[_EntryDraft]:
	namespace = key.split(":", 1)[0]
	base = [
		draft for draft in drafts
		if not draft.is_insertion and draft.source_id == namespace
	]
	base.sort(key=lambda draft: draft.source_id)
	base_ids = {id(draft) for draft in base}
	rest = [draft for draft in drafts if id(draft) not in base_ids]
	rest.sort(key=lambda draft: (_approval_date(sources, draft.source_id) or date.min, draft.source_id))
	return base + rest


def _finalize_entries(
	ordered: list[_EntryDraft],
	sources: dict[str, Any],
) -> tuple[TimelineEntry, ...]:
	entries: list[TimelineEntry] = []
	previous_char_count: int | None = None
	for draft in ordered:
		ratio = None
		if previous_char_count:
			ratio = draft.char_count / previous_char_count
		entries.append(
			TimelineEntry(
				source_id=draft.source_id,
				approval_date=_date_string(sources, draft.source_id),
				provision_id=draft.provision_id,
				unit_labels=draft.unit_labels,
				is_insertion=draft.is_insertion,
				provision_partial=draft.provision_partial,
				char_count=draft.char_count,
				length_ratio=ratio,
			)
		)
		previous_char_count = draft.char_count
	return tuple(entries)
