"""CRAG facet-checker prompt contract + Phase 5 CP1 paid-call cache.

Lives in ``app.retriever`` (not ``app.pipeline`` or ``app.evals``) because both
layers need it: ``app.pipeline.evidence`` (the live/legacy checker call and the
Phase 5 CP2 cache-routed checker used by the ``global_rerank`` eval arm) and
``app.evals.facet_audit`` (the CP1 offline audit that originally built this
cache). ``app.pipeline`` may import ``app.retriever`` but never ``app.evals``
(tests/unit/test_import_boundaries.py); this module has no pipeline import, so
placing the cache mechanics here — rather than only in ``app.evals`` — is what
makes the pipeline-side cache routing possible without violating that
boundary.

Content-addressed cache key derivation, cache file layout, and the pending
O_EXCL-marker discipline are unchanged from the original CP1 implementation:
existing cached decisions under ``data/eval_results/facet_audit_cache/`` stay
valid (this is a pure move, not a rework — ``FACET_AUDIT_CONTRACT_VERSION``
and the prompt text are unchanged, so the cache key derivation is identical).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from app.retriever.context_builder import build_context
from app.retriever.types import RetrievalResult

FACET_AUDIT_CONTRACT_VERSION = 1
FACET_AUDIT_MODEL = "claude-haiku-4-5"
FACET_AUDIT_MAX_TOKENS = 512
FACET_AUDIT_TIMEOUT_SECONDS = 30.0

_CRAG_SYSTEM = """You are a facet checker for a Philippine-law retrieval system.
A "facet" is a SUBSTANTIVE legal element the question needs answered — a rule,
element, penalty, requirement, or exception. It is NOT a matter of wording.

List the facets the question needs, then check whether the passages supply the
substance of each (the rule/number/element itself), even if worded differently.

A facet is MISSING only if the passages do not contain the substantive law needed
to answer it. Do NOT flag a facet as missing for any of these reasons:
- the passages don't cite a specific article/section number
- the passages don't state the rule in one consolidated sentence
- you would prefer a fuller, more exhaustive, or more definitive phrasing
- the answer must be inferred by combining two passages
If every needed rule/element is present in substance, return sufficient.
When uncertain, prefer sufficient. Never return insufficient.

Reply in exactly this format:
FACETS: <semicolon-separated substantive facets the question needs>
PRESENT: <semicolon-separated facets whose substance the passages supply>
MISSING: <semicolon-separated facets whose substance is absent; write "none" if all present>
VERDICT: sufficient | partial"""


# Judges routinely fill MISSING with a "no gaps" sentinel instead of leaving it
# blank; treat those as empty so they don't count as a missing facet (→ partial).
_NULL_FACETS = {"none", "nothing", "n/a", "na", "n.a.", "-", "no missing facets"}


def _split_facets(value: str) -> list[str]:
    return [
        part.strip()
        for part in value.split(";")
        if part.strip() and part.strip().lower() not in _NULL_FACETS
    ]


def _parse_crag_output(output: str) -> tuple[list[str], list[str], list[str], str] | None:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        if key in {"FACETS", "PRESENT", "MISSING", "VERDICT"}:
            fields[key] = value.strip()

    verdict = fields.get("VERDICT", "").lower()
    if verdict not in {"sufficient", "partial"}:
        return None

    facets = _split_facets(fields.get("FACETS", ""))
    present = _split_facets(fields.get("PRESENT", ""))
    missing = _split_facets(fields.get("MISSING", ""))
    final_verdict = "partial" if missing else "sufficient"
    return facets, present, missing, final_verdict


def _render_crag_prompt(question: str, chunks: list[RetrievalResult]) -> str:
    """Render the CRAG facet-checker user prompt. Shared by the live checker
    (app.pipeline.evidence), the Phase 5 cache-routed checker, and the offline
    Phase 5 CP1 audit (app.evals.facet_audit) so all paths run the identical
    contract."""
    context_block, _ = build_context(chunks)
    return f"""Passages:
{context_block}

Question: {question}

FACETS:"""


# ---------------------------------------------------------------------------
# Cache: mirrors app/retriever/legal_query_rewriter.py discipline exactly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FacetAuditDecision:
    rendered_prompt_hash: str
    model: str
    prompt_contract_hash: str
    verdict: Literal["sufficient", "partial"]
    facets: list[str]
    present: list[str]
    missing: list[str]
    operational_fallback: bool
    judge_output_hash: str | None
    judge_error: str | None
    call_latency_ms: float | None
    cache_key: str
    cache_status: Literal["miss_written", "hit", "pending_recovered"]


def facet_audit_prompt_contract_hash() -> str:
    """Stable identity of the (reused, unforked) CRAG prompt contract."""
    from app.evals.integrity import sha256

    return sha256(
        {
            "contract_version": FACET_AUDIT_CONTRACT_VERSION,
            "system": _CRAG_SYSTEM,
            "max_tokens": FACET_AUDIT_MAX_TOKENS,
        }
    )


def facet_audit_cache_key(rendered_prompt: str, *, model: str = FACET_AUDIT_MODEL) -> str:
    from app.evals.integrity import sha256, text_sha256

    return sha256(
        {
            "contract_version": FACET_AUDIT_CONTRACT_VERSION,
            "prompt_contract_hash": facet_audit_prompt_contract_hash(),
            "model": model,
            "rendered_prompt_hash": text_sha256(rendered_prompt),
        }
    )


def _cache_dir() -> Path:
    from app.config import settings

    return (
        Path(settings.eval_results_dir)
        / "facet_audit_cache"
        / f"v{FACET_AUDIT_CONTRACT_VERSION}"
    )


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_pending(path: Path, key: str) -> bool:
    from app.evals.integrity import canonical_json

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, canonical_json({"cache_key": key, "state": "pending"}))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)
    return True


def _atomic_write_decision(path: Path, decision: FacetAuditDecision) -> None:
    from app.evals.integrity import canonical_json

    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_json(asdict(decision)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _decision_is_valid(decision: FacetAuditDecision, *, cache_key: str, model: str) -> bool:
    if (
        decision.cache_key != cache_key
        or decision.model != model
        or decision.prompt_contract_hash != facet_audit_prompt_contract_hash()
        or decision.cache_status not in {"miss_written", "pending_recovered"}
    ):
        return False
    if decision.operational_fallback:
        return (
            decision.verdict == "sufficient"
            and decision.facets == []
            and decision.present == []
            and decision.missing == []
        )
    if decision.verdict not in {"sufficient", "partial"}:
        return False
    if decision.verdict == "partial" and not decision.missing:
        return False
    if decision.verdict == "sufficient" and decision.missing:
        return False
    return True


def _read_cached(path: Path, *, cache_key: str, model: str) -> FacetAuditDecision | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        decision = FacetAuditDecision(**payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return decision if _decision_is_valid(decision, cache_key=cache_key, model=model) else None


def cached_decision(
    rendered_prompt: str, *, model: str = FACET_AUDIT_MODEL
) -> FacetAuditDecision | None:
    """Read-only cache lookup. Never touches the network or writes any marker."""
    key = facet_audit_cache_key(rendered_prompt, model=model)
    final_path = _cache_dir() / f"{key}.json"
    if not final_path.exists():
        return None
    cached = _read_cached(final_path, cache_key=key, model=model)
    if cached is None:
        return None
    return FacetAuditDecision(**{**asdict(cached), "cache_status": "hit"})


def _call_haiku(rendered_prompt: str, *, model: str) -> str:
    import anthropic  # lazy: this must not affect ordinary startup

    from app.config import settings

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=FACET_AUDIT_TIMEOUT_SECONDS,
        max_retries=0,
    )
    response = client.messages.create(
        model=model,
        max_tokens=FACET_AUDIT_MAX_TOKENS,
        temperature=0,
        system=_CRAG_SYSTEM,
        messages=[{"role": "user", "content": rendered_prompt}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _pending_recovery(
    rendered_prompt: str, key: str, *, final_path: Path, pending_path: Path, model: str
) -> FacetAuditDecision:
    # A pending (or malformed final) marker proves an earlier paid request may
    # have completed. Fall back durably instead of risking a second call.
    from app.evals.integrity import text_sha256

    decision = FacetAuditDecision(
        rendered_prompt_hash=text_sha256(rendered_prompt),
        model=model,
        prompt_contract_hash=facet_audit_prompt_contract_hash(),
        verdict="sufficient",
        facets=[],
        present=[],
        missing=[],
        operational_fallback=True,
        judge_output_hash=None,
        judge_error="interrupted_after_request",
        call_latency_ms=None,
        cache_key=key,
        cache_status="pending_recovered",
    )
    _atomic_write_decision(final_path, decision)
    pending_path.unlink(missing_ok=True)
    return decision


def call_and_cache(
    rendered_prompt: str, *, model: str = FACET_AUDIT_MODEL
) -> FacetAuditDecision:
    """Full cached call: O_EXCL pending marker, one paid call, atomic finalize."""
    from app.evals.integrity import text_sha256

    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = facet_audit_cache_key(rendered_prompt, model=model)
    final_path = cache_dir / f"{key}.json"
    pending_path = cache_dir / f"{key}.pending.json"

    if final_path.exists():
        cached = _read_cached(final_path, cache_key=key, model=model)
        if cached is not None:
            return FacetAuditDecision(**{**asdict(cached), "cache_status": "hit"})
        return _pending_recovery(
            rendered_prompt, key, final_path=final_path, pending_path=pending_path, model=model
        )
    if pending_path.exists():
        return _pending_recovery(
            rendered_prompt, key, final_path=final_path, pending_path=pending_path, model=model
        )
    if not _write_pending(pending_path, key):
        return _pending_recovery(
            rendered_prompt, key, final_path=final_path, pending_path=pending_path, model=model
        )

    started = time.perf_counter()
    try:
        raw_output = _call_haiku(rendered_prompt, model=model)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        parsed = _parse_crag_output(raw_output)
        if parsed is None:
            decision = FacetAuditDecision(
                rendered_prompt_hash=text_sha256(rendered_prompt),
                model=model,
                prompt_contract_hash=facet_audit_prompt_contract_hash(),
                verdict="sufficient",
                facets=[],
                present=[],
                missing=[],
                operational_fallback=True,
                judge_output_hash=text_sha256(raw_output) if raw_output else None,
                judge_error="unparseable_output",
                call_latency_ms=latency_ms,
                cache_key=key,
                cache_status="miss_written",
            )
        else:
            facets, present, missing, verdict = parsed
            decision = FacetAuditDecision(
                rendered_prompt_hash=text_sha256(rendered_prompt),
                model=model,
                prompt_contract_hash=facet_audit_prompt_contract_hash(),
                verdict=verdict,
                facets=facets,
                present=present,
                missing=missing,
                operational_fallback=False,
                judge_output_hash=text_sha256(raw_output),
                judge_error=None,
                call_latency_ms=latency_ms,
                cache_key=key,
                cache_status="miss_written",
            )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        decision = FacetAuditDecision(
            rendered_prompt_hash=text_sha256(rendered_prompt),
            model=model,
            prompt_contract_hash=facet_audit_prompt_contract_hash(),
            verdict="sufficient",
            facets=[],
            present=[],
            missing=[],
            operational_fallback=True,
            judge_output_hash=None,
            judge_error=f"{type(exc).__name__}: {exc}",
            call_latency_ms=latency_ms,
            cache_key=key,
            cache_status="miss_written",
        )

    _atomic_write_decision(final_path, decision)
    pending_path.unlink(missing_ok=True)
    return decision
