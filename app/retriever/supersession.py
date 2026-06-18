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
    base_provisions: tuple[str, ...]
    operative_source_id: str
    operative_provisions: tuple[str, ...]
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
            base_provisions=tuple(r.get("base_provisions", [])),
            operative_source_id=r["operative_source_id"],
            operative_provisions=tuple(r.get("operative_provisions", [])),
            kind=r.get("kind", "amendment"),
        )
        for r in data.get("supersessions", [])
    )


def provision_matches(unit_label: str | None, provisions) -> bool:
    """A chunk's unit_label matches provision P if it IS P or a sub-item of P
    (Section 21 / Section 21(1)), but not a numeric neighbor (Section 210)."""
    if not unit_label:
        return False
    return any(unit_label == p or unit_label.startswith(p + "(") for p in provisions)
