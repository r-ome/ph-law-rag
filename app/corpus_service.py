from pathlib import Path

import yaml

from app.config import SourceConfig, SourceFile, settings
from app.db import get_document, list_documents


def _source_map() -> dict[str, SourceConfig]:
    path = Path(settings.source_config_path)
    data = yaml.safe_load(path.read_text()) or {}
    parsed = SourceFile.model_validate(data)
    return {s.source_id: s for s in parsed.sources}


def list_documents_enriched() -> list[dict]:
    smap = _source_map()
    out: list[dict] = []
    for row in list_documents():
        src = smap.get(row["source_id"])
        out.append(
            {
                **row,
                "status": src.status if src else "unknown",
                "source_index": src.source_index if src else None,
                "official_number": src.official_number if src else None,
                "tags": src.tags if src else [],
            }
        )
    return out


def get_document_detail(doc_id: str) -> dict | None:
    row = get_document(doc_id)
    if row is None:
        return None

    normalized_path = row.pop("normalized_path", None)
    text = ""
    if normalized_path and Path(normalized_path).exists():
        text = Path(normalized_path).read_text(encoding="utf-8")

    src = _source_map().get(row["source_id"])
    return {
        **row,
        "normalized_text": text,
        "status": src.status if src else "unknown",
        "source_index": src.source_index if src else None,
        "official_number": src.official_number if src else None,
        "tags": src.tags if src else [],
        "approval_date": src.approval_date.isoformat() if src and src.approval_date else None,
        "effectivity_date": (
            src.effectivity_date.isoformat() if src and src.effectivity_date else None
        ),
        "availability": src.availability if src else None,
        "structure": src.structure if src else None,
        "notes": src.notes if src else None,
        "amends": src.amends if src else [],
        "repeals": src.repeals if src else [],
        "supersedes": src.supersedes if src else [],
        "implements": src.implements if src else [],
        "amends_namespace": src.amends_namespace if src else None,
    }
