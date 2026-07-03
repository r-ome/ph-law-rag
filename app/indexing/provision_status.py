"""Provision-level operability overrides — index-time policy data.

Marks individual base provisions as no longer current law (whole-provision repeal /
reclassification) so retrieval suppresses them, WITHOUT needing the amending provision to be
labeled (the "amendment-label wall"). Leaf-scoped overrides can hide specific unit_label values;
surviving sibling chunks are stamped with parent_has_hidden_leaves so parent expansion does not
swap in parent text containing hidden leaves. Applied at index time onto chunk metadata; retrieval
only ever consumes the resulting operability_action payload. Kept under indexing/ (not retriever/)
because that's where it is applied — retrieval has no dependency on this module or the YAML.

Distinct from app/retriever/supersession.py: that is a query-time REORDER policy (prefer the
operative chunk when both are retrieved); this is an index-time STATUS policy (this provision is
not current). Different mechanism, different file.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings


@dataclass(frozen=True)
class ProvisionOverride:
	provision_id: str
	provision_status: str
	operability_action: str
	basis_source_id: str | None
	effective_date: str | None
	note: str | None
	source_id: str | None = None
	unit_labels: tuple[str, ...] | None = None


@lru_cache(maxsize=1)
def load_provision_overrides() -> dict[str, tuple[ProvisionOverride, ...]]:
	path = Path(settings.provision_status_path)
	if not path.exists():
		return {}
	data = yaml.safe_load(path.read_text()) or {}
	grouped: dict[str, list[ProvisionOverride]] = {}
	rows = list(data.get("overrides", []))
	for generated in data.get("generated_overrides", []):
		for provision_id in generated.get("provision_ids", []):
			row = dict(generated)
			row.pop("provision_ids", None)
			row["provision_id"] = provision_id
			rows.append(row)
	for r in rows:
		pid = r["provision_id"]
		labels = r.get("unit_labels")
		grouped.setdefault(pid, []).append(ProvisionOverride(
			provision_id=pid,
			source_id=r.get("source_id"),
			unit_labels=tuple(labels) if labels else None,
			provision_status=r.get("provision_status", "superseded"),
			operability_action=r.get("operability_action", "hide"),
			basis_source_id=r.get("basis_source_id"),
			effective_date=r.get("effective_date"),
			note=r.get("note"),
		))
	return {pid: tuple(rules) for pid, rules in grouped.items()}


def apply_overrides(metadata: dict, overrides: dict[str, tuple[ProvisionOverride, ...]] | None = None) -> dict:
	"""Stamp provision-level operability onto a chunk's metadata, in place. Matches on
	provision_id. Sets provision_status + operability_action + operability_basis_source_id —
	NEVER the document-level `status`. No-op for chunks without a matching provision_id
	(prose/amendment chunks, or provisions with no override). Returns the same dict."""
	if overrides is None:
		overrides = load_provision_overrides()
	rules = overrides.get(metadata.get("provision_id"))
	if rules is None:
		return metadata
	survived_leaf_rule = False
	for rule in rules:
		if rule.source_id and rule.source_id != metadata.get("source_id"):
			continue
		if rule.unit_labels and metadata.get("unit_label") not in rule.unit_labels:
			survived_leaf_rule = True
			continue
		metadata["provision_status"] = rule.provision_status
		metadata["operability_action"] = rule.operability_action
		if rule.basis_source_id is not None:
			metadata["operability_basis_source_id"] = rule.basis_source_id
		return metadata
	if survived_leaf_rule:
		metadata["parent_has_hidden_leaves"] = 1
	return metadata
