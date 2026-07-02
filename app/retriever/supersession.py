"""Provision-level supersession map — retrieval policy data for operative-law preference.

Kept retrieval-local (not in config.py) because this is policy about which provisions
replace which, not global settings or the source manifest. v1 covers amendment
supersession only; repeal_replace rules can be added later.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings


@dataclass(frozen=True)
class SupersessionRule:
    base_source_id: str
    base_provision_ids: tuple[str, ...]
    operative_source_id: str
    operative_provision_ids: tuple[str, ...]
    kind: str


@lru_cache(maxsize=1)
def load_supersessions() -> tuple[SupersessionRule, ...]:
    path = Path(settings.provision_supersession_path)
    if not path.exists():
        return ()
    data = yaml.safe_load(path.read_text()) or {}
    return tuple(
        SupersessionRule(
            base_source_id=r["base_source_id"],
            base_provision_ids=tuple(r.get("base_provision_ids", [])),
            operative_source_id=r["operative_source_id"],
            operative_provision_ids=tuple(r.get("operative_provision_ids", [])),
            kind=r.get("kind", "amendment"),
        )
        for r in data.get("supersessions", [])
    )


def provision_matches(provision_id: str | None, provision_ids) -> bool:
    """Exact provision_id match. Enumeration leaves inherit their parent provision_id."""
    if not provision_id:
        return False
    return provision_id in provision_ids
