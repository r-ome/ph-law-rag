"""Pure, score-agnostic final context packaging."""

from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.retriever.context_builder import build_context
from app.retriever.types import RetrievalResult

ADAPTIVE_CONTEXT_CONTRACT_VERSION = 2
ADAPTIVE_CONTEXT_TOKEN_ESTIMATOR = "rendered_chars_div4_v1"
ADAPTIVE_CONTEXT_DEFAULTS = {
    "adaptive_context_enabled": True,
    "adaptive_context_contract_version": ADAPTIVE_CONTEXT_CONTRACT_VERSION,
    "adaptive_context_floor": 4,
    "adaptive_context_base_cap": 7,
    "adaptive_context_uncertain_cap": 11,
    "adaptive_context_multifacet_cap": 11,
    "adaptive_context_stabilization_patience": 2,
    "adaptive_context_token_target": 2400,
    "adaptive_context_token_estimator": ADAPTIVE_CONTEXT_TOKEN_ESTIMATOR,
}

SELECTOR_CONSUMED_FIELDS = {
    "top_level": ("chunk_id", "text"),
    "metadata": (
        "source_id",
        "doc_id",
        "url",
        "title",
        "provision_id",
        "structure_path",
        "unit_type",
        "unit_label",
        "parent_key",
        "sibling_seed_chunk_id",
        "dedup_merged_chunk_ids",
        "is_structural",
        "_edge_relation",
    ),
}


def _selector_metadata(result: RetrievalResult) -> dict:
    return {
        key: result.metadata.get(key)
        for key in SELECTOR_CONSUMED_FIELDS["metadata"]
    }


def selector_semantic_record(result: RetrievalResult) -> dict:
    from app.evals.integrity import text_sha256

    return {
        "chunk_id": result.chunk_id,
        "text_sha256": text_sha256(result.text),
        "metadata": _selector_metadata(result),
    }


def full_record(result: RetrievalResult) -> dict:
    return {
        "chunk_id": result.chunk_id,
        "text": result.text,
        "score": result.score,
        "metadata": dict(result.metadata),
    }


def packaging_pool_semantic_hash(results: list[RetrievalResult]) -> str:
    from app.evals.integrity import ordered_hash

    return ordered_hash([selector_semantic_record(result) for result in results])


def packaging_pool_full_hash(results: list[RetrievalResult]) -> str:
    from app.evals.integrity import ordered_hash

    return ordered_hash([full_record(result) for result in results])


@dataclass(frozen=True)
class AdaptiveContextSignals:
    """Observable signals supplied by, or inferred from, a frozen candidate pool."""

    accepted_legal_rewrite: bool = False
    synthesis_detected: bool = False
    coverage_uncertain: bool = False


@dataclass(frozen=True)
class AdaptiveContextDiagnostics:
    input_count: int
    deduplicated_count: int
    selected_count: int
    cap: int
    rendered_tokens: int
    token_target: int
    token_overflow: int
    chunk_cap_overflow: int
    duplicate_chunk_ids_removed: int
    represented_chunks_removed: int
    duplicate_texts_removed: int
    bundles_considered: int
    bundles_selected: int
    non_novel_bundles: int
    stop_reason: str
    signals: AdaptiveContextSignals

    def as_dict(self) -> dict:
        return {
            "contract_version": ADAPTIVE_CONTEXT_CONTRACT_VERSION,
            "token_estimator": ADAPTIVE_CONTEXT_TOKEN_ESTIMATOR,
            "input_count": self.input_count,
            "deduplicated_count": self.deduplicated_count,
            "selected_count": self.selected_count,
            "cap": self.cap,
            "rendered_tokens": self.rendered_tokens,
            "token_target": self.token_target,
            "token_overflow": self.token_overflow,
            "chunk_cap_overflow": self.chunk_cap_overflow,
            "duplicate_chunk_ids_removed": self.duplicate_chunk_ids_removed,
            "represented_chunks_removed": self.represented_chunks_removed,
            "duplicate_texts_removed": self.duplicate_texts_removed,
            "bundles_considered": self.bundles_considered,
            "bundles_selected": self.bundles_selected,
            "non_novel_bundles": self.non_novel_bundles,
            "stop_reason": self.stop_reason,
            "signals": {
                "accepted_legal_rewrite": self.signals.accepted_legal_rewrite,
                "synthesis_detected": self.signals.synthesis_detected,
                "coverage_uncertain": self.signals.coverage_uncertain,
            },
        }


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalized_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), parts.query, "")
    )


def _source_identity(result: RetrievalResult) -> tuple | None:
    metadata = _selector_metadata(result)
    for name in ("source_id", "doc_id"):
        value = metadata.get(name)
        if value:
            return (name, str(value))
    url = _normalized_url(metadata.get("url"))
    if url:
        return ("url", url)
    title = _normalized_text(str(metadata.get("title") or ""))
    return ("title", title) if title else None


def _provision_identity(result: RetrievalResult) -> tuple | None:
    source = _source_identity(result)
    metadata = _selector_metadata(result)
    provision_id = metadata.get("provision_id")
    if provision_id:
        return ("provision", source, str(provision_id))
    structure_path = metadata.get("structure_path")
    unit_type = metadata.get("unit_type")
    unit_label = metadata.get("unit_label")
    if structure_path or unit_type or unit_label:
        return (
            "structure",
            source,
            str(structure_path or ""),
            str(unit_type or ""),
            str(unit_label or ""),
        )
    return None


def _family_identity(result: RetrievalResult) -> tuple | None:
    metadata = _selector_metadata(result)
    if metadata.get("parent_key"):
        return ("parent", str(metadata["parent_key"]))
    if metadata.get("structure_path"):
        return ("path", _source_identity(result), str(metadata["structure_path"]))
    provision = _provision_identity(result)
    return ("provision_family", provision) if provision else None


def _leaf_identity(result: RetrievalResult) -> tuple | None:
    metadata = _selector_metadata(result)
    label = metadata.get("unit_label")
    if not label:
        return None
    parent_key = metadata.get("parent_key")
    if parent_key:
        return ("parent_leaf", str(parent_key), str(label))
    provision = _provision_identity(result)
    return ("provision_leaf", provision, str(label)) if provision else None


def _novelty(result: RetrievalResult) -> set[tuple]:
    # chunk_id is deliberately absent: it is an opaque grouping/tracing fallback,
    # not evidence novelty after the mandatory floor.
    return {
        identity
        for identity in (
            _source_identity(result),
            _provision_identity(result),
            _family_identity(result),
            _leaf_identity(result),
        )
        if identity is not None
    }


def estimate_rendered_tokens(results: list[RetrievalResult]) -> int:
    """Estimate the fully rendered context uniformly from text and citation headers."""
    rendered, _ = build_context(results)
    return math.ceil(len(rendered) / 4)


def infer_structural_signals(
    results: list[RetrievalResult],
    *,
    accepted_legal_rewrite: bool = False,
    synthesis_detected: bool = False,
) -> AdaptiveContextSignals:
    """Infer widening only from observable pool structure.

    Three or more sources is a conservative synthesis signal. A pool is uncertain
    when the mandatory first four chunks expose one source while later candidates
    expose another. Neither rule reads the question's hidden eval classification.
    """
    sources = {_source_identity(result) for result in results}
    sources.discard(None)
    floor_sources = {_source_identity(result) for result in results[:4]}
    floor_sources.discard(None)
    structural_synthesis = len(sources) >= 3
    structural_uncertainty = len(sources) > len(floor_sources) and len(floor_sources) <= 1
    return AdaptiveContextSignals(
        accepted_legal_rewrite=accepted_legal_rewrite,
        synthesis_detected=synthesis_detected or structural_synthesis,
        coverage_uncertain=accepted_legal_rewrite or structural_uncertainty,
    )


def _defensive_dedup(
    indexed: list[tuple[int, RetrievalResult]],
) -> tuple[list[tuple[int, RetrievalResult]], tuple[int, int, int]]:
    """Remove only explicit duplicates while retaining dangling sibling groups."""
    represented: set[str] = set()
    for _, result in indexed:
        represented.update(
            str(chunk_id)
            for chunk_id in (_selector_metadata(result).get("dedup_merged_chunk_ids") or [])
            if str(chunk_id) != result.chunk_id
        )

    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    output: list[tuple[int, RetrievalResult]] = []
    duplicate_ids = represented_count = duplicate_texts = 0
    for index, result in indexed:
        if result.chunk_id in seen_ids:
            duplicate_ids += 1
            continue
        seen_ids.add(result.chunk_id)
        if result.chunk_id in represented:
            represented_count += 1
            continue
        normalized = _normalized_text(result.text)
        if normalized and normalized in seen_texts:
            duplicate_texts += 1
            continue
        if normalized:
            seen_texts.add(normalized)
        output.append((index, result))
    return output, (duplicate_ids, represented_count, duplicate_texts)


def _atomic_bundles(results: list[RetrievalResult]) -> list[list[RetrievalResult]]:
    """Build seed-centered bundles before dedup, then retain surviving members.

    A sibling seed ID remains a valid grouping key when its seed is absent. A
    bundle fires at the first surviving member and its members retain pool order.
    """
    indexed = list(enumerate(results))
    referenced_seed_ids = {
        str(_selector_metadata(result)["sibling_seed_chunk_id"])
        for _, result in indexed
        if _selector_metadata(result).get("sibling_seed_chunk_id")
    }
    original_group: dict[int, tuple[str, str]] = {}
    for index, result in indexed:
        seed = _selector_metadata(result).get("sibling_seed_chunk_id")
        if seed:
            original_group[index] = ("sibling_seed", str(seed))
        elif result.chunk_id in referenced_seed_ids:
            original_group[index] = ("sibling_seed", result.chunk_id)
        else:
            original_group[index] = ("chunk", result.chunk_id)

    survivors, _ = _defensive_dedup(indexed)
    grouped: dict[tuple[str, str], list[tuple[int, RetrievalResult]]] = {}
    first: dict[tuple[str, str], int] = {}
    for index, result in survivors:
        key = original_group[index]
        grouped.setdefault(key, []).append((index, result))
        first.setdefault(key, index)
    return [
        [result for _, result in sorted(grouped[key])]
        for key in sorted(grouped, key=lambda item: first[item])
    ]


def select_adaptive_context(
    results: list[RetrievalResult],
    *,
    signals: AdaptiveContextSignals | None = None,
    floor: int = 4,
    base_cap: int = 7,
    uncertain_cap: int = 11,
    multifacet_cap: int = 11,
    stabilization_patience: int = 2,
    token_target: int = 2400,
) -> tuple[list[RetrievalResult], AdaptiveContextDiagnostics]:
    """Select whole evidence bundles without comparing backend score values."""
    if min(floor, base_cap, uncertain_cap, multifacet_cap) < 1:
        raise ValueError("adaptive context floor and caps must be positive")
    if not floor <= base_cap <= uncertain_cap <= multifacet_cap:
        raise ValueError("adaptive context bounds must be monotonic")
    if stabilization_patience < 1 or token_target < 1:
        raise ValueError("adaptive context patience and token target must be positive")

    indexed = list(enumerate(results))
    survivors, removed = _defensive_dedup(indexed)
    survivor_results = [result for _, result in survivors]
    bundles = _atomic_bundles(results)
    signals = signals or infer_structural_signals(survivor_results)
    cap = (
        multifacet_cap
        if signals.synthesis_detected
        else uncertain_cap
        if signals.coverage_uncertain or signals.accepted_legal_rewrite
        else base_cap
    )

    selected: list[RetrievalResult] = []
    observed: set[tuple] = set()
    non_novel_run = 0
    non_novel_total = 0
    bundles_considered = 0
    bundles_selected = 0
    stop_reason = "exhausted"
    for bundle in bundles:
        if len(selected) >= cap and len(selected) >= floor:
            stop_reason = "cap"
            break
        bundles_considered += 1
        bundle_novelty = set().union(*(_novelty(result) for result in bundle))
        mandatory = len(selected) < floor
        novel = bool(bundle_novelty - observed)
        if not mandatory and not novel:
            non_novel_total += 1
            non_novel_run += 1
            if non_novel_run >= stabilization_patience:
                stop_reason = "stabilized"
                break
            continue

        tentative = [*selected, *bundle]
        tentative_tokens = estimate_rendered_tokens(tentative)
        # The budget is soft at both the mandatory floor and an already-admitted
        # atomic boundary. Admit the whole bundle, report overflow, then stop.
        selected = tentative
        bundles_selected += 1
        observed.update(bundle_novelty)
        non_novel_run = 0
        if tentative_tokens > token_target and len(selected) >= floor:
            stop_reason = "token_target"
            break

    rendered_tokens = estimate_rendered_tokens(selected)
    diagnostics = AdaptiveContextDiagnostics(
        input_count=len(results),
        deduplicated_count=len(survivor_results),
        selected_count=len(selected),
        cap=cap,
        rendered_tokens=rendered_tokens,
        token_target=token_target,
        token_overflow=max(0, rendered_tokens - token_target),
        chunk_cap_overflow=max(0, len(selected) - cap),
        duplicate_chunk_ids_removed=removed[0],
        represented_chunks_removed=removed[1],
        duplicate_texts_removed=removed[2],
        bundles_considered=bundles_considered,
        bundles_selected=bundles_selected,
        non_novel_bundles=non_novel_total,
        stop_reason=stop_reason,
        signals=signals,
    )
    return selected, diagnostics
