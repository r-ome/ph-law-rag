"""Provision-level operability overrides — index-time policy data.

Marks individual base provisions as no longer current law (whole-provision repeal /
reclassification) so retrieval suppresses them, WITHOUT needing the amending provision to be
labeled (the "amendment-label wall"). Applied at index time onto chunk metadata; retrieval only
ever consumes the resulting operability_action payload. Kept under indexing/ (not retriever/)
because that's where it is applied — retrieval has no dependency on this module.

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


@lru_cache(maxsize=1)
def load_provision_overrides() -> dict[str, ProvisionOverride]:
	path = Path(settings.provision_status_path)
	if not path.exists():
		return {}
	data = yaml.safe_load(path.read_text()) or {}
	overrides: dict[str, ProvisionOverride] = {}
	for r in data.get("overrides", []):
		pid = r["provision_id"]
		overrides[pid] = ProvisionOverride(
			provision_id=pid,
			provision_status=r.get("provision_status", "superseded"),
			operability_action=r.get("operability_action", "hide"),
			basis_source_id=r.get("basis_source_id"),
			effective_date=r.get("effective_date"),
			note=r.get("note"),
		)
	return overrides


def apply_overrides(metadata: dict, overrides: dict[str, ProvisionOverride] | None = None) -> dict:
	"""Stamp provision-level operability onto a chunk's metadata, in place. Matches on
	provision_id. Sets provision_status + operability_action + operability_basis_source_id —
	NEVER the document-level `status`. No-op for chunks without a matching provision_id
	(prose/amendment chunks, or provisions with no override). Returns the same dict."""
	if overrides is None:
		overrides = load_provision_overrides()
	o = overrides.get(metadata.get("provision_id"))
	if o is None:
		return metadata
	metadata["provision_status"] = o.provision_status
	metadata["operability_action"] = o.operability_action
	if o.basis_source_id is not None:
		metadata["operability_basis_source_id"] = o.basis_source_id
	return metadata
