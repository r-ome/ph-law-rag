"""Strict, cached legal-query rewrite seam for the offline Phase 2 experiment.

This module intentionally has no production-pipeline imports.  A later checkpoint
may call it from the CLI-only experiment arm, but serving remains original-only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal

from app.config import settings


LEGAL_REWRITE_CONTRACT_VERSION = 1
LEGAL_REWRITE_PROMPT_VERSION = "v3"
LEGAL_REWRITE_MAX_TOKENS = 160
LEGAL_REWRITE_DELIMITER = " | Legal terms: "
LEGAL_REWRITE_ASSISTANT_PREFILL = '{"legal_query":"'
LEGAL_REWRITE_RESPONSE_RECONSTRUCTION = "assistant_prefill + model_text_continuation"

LEGAL_REWRITE_SYSTEM_PROMPT = (
    "You render a Philippine-law retrieval query.\n"
    "Your response is already prefilled with the exact bytes "
    "`{\"legal_query\":\"`. "
    "Continue after that prefill; do not repeat it. Together, the prefill and your "
    "continuation must form exactly one single-line raw JSON object and nothing "
    "else: no markdown fences, prose, leading/trailing whitespace, or line breaks. "
    "Use exactly three keys in this order: `legal_query` (a JSON string), "
    "`citations` (the empty JSON array `[]`), and `confidence` (a JSON string whose "
    "value is exactly `\"high\"` or `\"low\"`; never a number). Exact schema: "
    "`{\"legal_query\":\"...\",\"citations\":[],\"confidence\":\"high\"}`.\n"
    "Preserve the supplied source query verbatim at the start of `legal_query`, "
    "followed by ` | Legal terms: ` and one concise legal-language retrieval "
    "rendering. Never mention any statute number, act number, article number, "
    "section number, or case or docket number in the rendering. Describe the "
    "doctrine or legal concept in words instead. Never answer the question, "
    "invent a legal identifier, cite a source, offer alternatives, or use markdown."
)

_PROMPT_TEMPLATE = "Source query (preserve it verbatim): {source_query}"
_IDENTIFIER_RE = re.compile(
    r"\b(?P<kind>R\.?\s*A\.?|Republic\s+Act|B\.?\s*P\.?|Batas\s+Pambansa|"
    r"E\.?\s*O\.?|Executive\s+Order|Articles?|Arts?\.?|Sections?|Secs?\.?|"
    r"G\.?\s*R\.?)\s*(?:(?:No|Nos|Blg)\.?\s*)?"
    r"(?P<number>(?:[A-Za-z]+-)?\d+(?:[.-]\d+)*(?:\([A-Za-z0-9-]+\))*)",
    re.IGNORECASE,
)
_ANSWER_PROSE_RE = re.compile(
    r"^(?:yes|no)(?:\b|$)|"
    r"\b(?:the answer(?:\s+is)?|answer is|this means|it means|therefore|"
    r"you (?:can|should|must)|under philippine law|is (?:illegal|legal|liable|guilty)|"
    r"(?:is|are) (?:valid|void|enforceable|unenforceable)|may (?:file|sue|claim))\b",
    re.IGNORECASE,
)
_ALTERNATIVE_RE = re.compile(r"\b(?:alternative(?:ly)?|option\s+\d+)\b", re.IGNORECASE)
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CAPTURE_DEPTH: ContextVar[int] = ContextVar("legal_rewrite_capture_depth", default=0)


@dataclass(frozen=True)
class LegalRewriteDecision:
    source_query: str
    legal_query: str | None
    confidence: Literal["high", "low"] | None
    status: Literal["disabled", "accepted", "fallback"]
    parser_outcome: Literal["not_called", "valid", "invalid", "literal_violation"]
    fallback_reason: Literal[
        "disabled",
        "low_confidence",
        "invalid_output",
        "literal_violation",
        "timeout",
        "llm_error",
        "interrupted_after_request",
    ] | None
    model: str | None
    prompt_version: str
    prompt_hash: str
    raw_output_hash: str | None
    call_latency_ms: float | None
    cache_key: str | None
    cache_status: Literal["bypassed", "miss_written", "hit", "pending_recovered"]


class LegalRewriteCaptureBusy(RuntimeError):
    """Raised before key artifacts or paid requests when the capture lock is held."""


@dataclass(frozen=True)
class _ParseResult:
    legal_query: str | None
    confidence: Literal["high", "low"] | None
    outcome: Literal["valid", "invalid", "literal_violation"]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(value: str | bytes | object) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    elif not isinstance(value, bytes):
        value = _canonical_json(value)
    return hashlib.sha256(value).hexdigest()


def legal_rewrite_prompt_hash() -> str:
    """Stable identity of the contract prompt, excluding the source query."""
    return _sha256(
        {
            "contract_version": LEGAL_REWRITE_CONTRACT_VERSION,
            "prompt_version": LEGAL_REWRITE_PROMPT_VERSION,
            "system": LEGAL_REWRITE_SYSTEM_PROMPT,
            "user_template": _PROMPT_TEMPLATE,
            "assistant_prefill": LEGAL_REWRITE_ASSISTANT_PREFILL,
            "response_reconstruction": LEGAL_REWRITE_RESPONSE_RECONSTRUCTION,
        }
    )


def render_legal_rewrite_prompt(source_query: str) -> str:
    return _PROMPT_TEMPLATE.format(source_query=source_query)


def reconstruct_legal_rewrite_output(model_text_continuation: str) -> str:
    """Build the strict parser input from the pinned assistant prefill and continuation."""
    return LEGAL_REWRITE_ASSISTANT_PREFILL + model_text_continuation


def legal_rewrite_cache_key(source_query: str) -> str:
    return _sha256(
        {
            "contract_version": LEGAL_REWRITE_CONTRACT_VERSION,
            "prompt_version": LEGAL_REWRITE_PROMPT_VERSION,
            "prompt_hash": legal_rewrite_prompt_hash(),
            "model": settings.legal_query_rewrite_model,
            "max_tokens": LEGAL_REWRITE_MAX_TOKENS,
            "source_query": source_query,
        }
    )


def _identifier_key(match: re.Match[str]) -> tuple[str, str]:
    kind = re.sub(r"[^a-z]", "", match.group("kind").lower())
    if kind in {"ra", "republicact"}:
        kind = "ra"
    elif kind in {"bp", "bataspambansa"}:
        kind = "bp"
    elif kind in {"eo", "executiveorder"}:
        kind = "eo"
    elif kind in {"article", "articles", "art", "arts"}:
        kind = "article"
    elif kind in {"section", "sections", "sec", "secs"}:
        kind = "section"
    else:
        kind = "gr"
    number = re.sub(r"\s+", "", match.group("number").lower())
    return kind, number


def _has_new_identifier(source_query: str, legal_query: str) -> bool:
    source_ids = {_identifier_key(item) for item in _IDENTIFIER_RE.finditer(source_query)}
    rewritten_ids = {_identifier_key(item) for item in _IDENTIFIER_RE.finditer(legal_query)}
    return not rewritten_ids.issubset(source_ids)


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_legal_rewrite_output(source_query: str, raw_output: str) -> _ParseResult:
    """Validate the paid model's output without accepting any leniency."""
    if (
        not isinstance(raw_output, str)
        or not raw_output
        or raw_output != raw_output.strip()
        or "\n" in raw_output
        or "\r" in raw_output
    ):
        return _ParseResult(None, None, "invalid")
    try:
        payload = json.loads(raw_output, object_pairs_hook=_no_duplicate_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _ParseResult(None, None, "invalid")
    if not isinstance(payload, dict) or set(payload) != {"legal_query", "citations", "confidence"}:
        return _ParseResult(None, None, "invalid")
    legal_query = payload["legal_query"]
    confidence = payload["confidence"]
    if not isinstance(legal_query, str) or not isinstance(confidence, str):
        return _ParseResult(None, None, "invalid")
    if payload["citations"] != [] or confidence not in {"high", "low"}:
        return _ParseResult(None, None, "invalid")

    prefix = f"{source_query}{LEGAL_REWRITE_DELIMITER}"
    if (
        not legal_query.startswith(prefix)
        or len(legal_query) > len(source_query) + 300
        or "\n" in legal_query
        or "\r" in legal_query
    ):
        return _ParseResult(None, None, "literal_violation")
    suffix = legal_query[len(prefix) :]
    if (
        not suffix
        or suffix != suffix.strip()
        or LEGAL_REWRITE_DELIMITER.strip() in suffix
        or _ALTERNATIVE_RE.search(suffix)
    ):
        return _ParseResult(None, None, "literal_violation")
    if _has_new_identifier(source_query, legal_query) or _ANSWER_PROSE_RE.search(suffix):
        return _ParseResult(None, None, "literal_violation")
    return _ParseResult(legal_query, confidence, "valid")


def disabled_legal_rewrite_decision(source_query: str) -> LegalRewriteDecision:
    return LegalRewriteDecision(
        source_query=source_query,
        legal_query=None,
        confidence=None,
        status="disabled",
        parser_outcome="not_called",
        fallback_reason="disabled",
        model=None,
        prompt_version=LEGAL_REWRITE_PROMPT_VERSION,
        prompt_hash=legal_rewrite_prompt_hash(),
        raw_output_hash=None,
        call_latency_ms=None,
        cache_key=None,
        cache_status="bypassed",
    )


def _cache_dir() -> Path:
    return Path(settings.legal_query_rewrite_cache_dir) / f"v{LEGAL_REWRITE_CONTRACT_VERSION}"


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_pending(path: Path, key: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, _canonical_json({"cache_key": key, "state": "pending"}))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)
    return True


def _atomic_write_json(path: Path, value: object) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _cached_decision_is_valid(
    decision: LegalRewriteDecision,
    *,
    source_query: str,
    cache_key: str,
) -> bool:
    if (
        decision.source_query != source_query
        or decision.cache_key != cache_key
        or decision.model != settings.legal_query_rewrite_model
        or decision.prompt_version != LEGAL_REWRITE_PROMPT_VERSION
        or decision.prompt_hash != legal_rewrite_prompt_hash()
        or decision.cache_status not in {"miss_written", "pending_recovered"}
        or decision.status not in {"accepted", "fallback"}
        or (
            decision.raw_output_hash is not None
            and _HEX_SHA256_RE.fullmatch(decision.raw_output_hash) is None
        )
        or (
            decision.call_latency_ms is not None
            and (
                isinstance(decision.call_latency_ms, bool)
                or not isinstance(decision.call_latency_ms, (int, float))
                or decision.call_latency_ms < 0
            )
        )
    ):
        return False
    if decision.status == "accepted":
        if not (
            decision.legal_query is not None
            and decision.confidence == "high"
            and decision.parser_outcome == "valid"
            and decision.fallback_reason is None
            and decision.raw_output_hash is not None
            and decision.call_latency_ms is not None
            and decision.cache_status == "miss_written"
        ):
            return False
        reconstructed = json.dumps(
            {
                "legal_query": decision.legal_query,
                "citations": [],
                "confidence": "high",
            },
            separators=(",", ":"),
        )
        return parse_legal_rewrite_output(source_query, reconstructed).outcome == "valid"
    expected_parser_outcomes = {
        "low_confidence": "valid",
        "invalid_output": "invalid",
        "literal_violation": "literal_violation",
        "timeout": "not_called",
        "llm_error": "not_called",
        "interrupted_after_request": "not_called",
    }
    if not (
        decision.legal_query is None
        and decision.fallback_reason in expected_parser_outcomes
        and decision.parser_outcome == expected_parser_outcomes[decision.fallback_reason]
        and (
            decision.confidence == "low"
            if decision.fallback_reason == "low_confidence"
            else decision.confidence is None
        )
    ):
        return False
    if decision.fallback_reason == "interrupted_after_request":
        return (
            decision.cache_status == "pending_recovered"
            and decision.raw_output_hash is None
            and decision.call_latency_ms is None
        )
    if decision.cache_status != "miss_written" or decision.call_latency_ms is None:
        return False
    if decision.fallback_reason in {"timeout", "llm_error"}:
        return decision.raw_output_hash is None
    return decision.raw_output_hash is not None


def _read_cached(
    path: Path,
    *,
    source_query: str,
    cache_key: str,
) -> LegalRewriteDecision | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        decision = LegalRewriteDecision(**payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return (
        decision
        if _cached_decision_is_valid(
            decision,
            source_query=source_query,
            cache_key=cache_key,
        )
        else None
    )


def _call_haiku(rendered_prompt: str) -> str:
    import anthropic  # lazy: this experiment must not affect ordinary startup

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.legal_query_rewrite_timeout_seconds,
        max_retries=0,
    )
    response = client.messages.create(
        model=settings.legal_query_rewrite_model,
        max_tokens=LEGAL_REWRITE_MAX_TOKENS,
        temperature=0,
        system=LEGAL_REWRITE_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": rendered_prompt},
            {"role": "assistant", "content": LEGAL_REWRITE_ASSISTANT_PREFILL},
        ],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def _decision_from_parse(
    source_query: str,
    parsed: _ParseResult,
    *,
    raw_output_hash: str | None,
    call_latency_ms: float | None,
    cache_key: str,
) -> LegalRewriteDecision:
    if parsed.outcome != "valid":
        return LegalRewriteDecision(
            source_query, None, None, "fallback", parsed.outcome,
            "literal_violation" if parsed.outcome == "literal_violation" else "invalid_output",
            settings.legal_query_rewrite_model, LEGAL_REWRITE_PROMPT_VERSION,
            legal_rewrite_prompt_hash(), raw_output_hash, call_latency_ms, cache_key, "miss_written",
        )
    if parsed.confidence == "low":
        return LegalRewriteDecision(
            source_query, None, "low", "fallback", "valid", "low_confidence",
            settings.legal_query_rewrite_model, LEGAL_REWRITE_PROMPT_VERSION,
            legal_rewrite_prompt_hash(), raw_output_hash, call_latency_ms, cache_key, "miss_written",
        )
    return LegalRewriteDecision(
        source_query, parsed.legal_query, "high", "accepted", "valid", None,
        settings.legal_query_rewrite_model, LEGAL_REWRITE_PROMPT_VERSION,
        legal_rewrite_prompt_hash(), raw_output_hash, call_latency_ms, cache_key, "miss_written",
    )


def _fallback_decision(
    source_query: str,
    reason: Literal["timeout", "llm_error"],
    key: str,
    *,
    call_latency_ms: float,
) -> LegalRewriteDecision:
    return LegalRewriteDecision(
        source_query, None, None, "fallback", "not_called", reason,
        settings.legal_query_rewrite_model, LEGAL_REWRITE_PROMPT_VERSION,
        legal_rewrite_prompt_hash(), None, call_latency_ms, key, "miss_written",
    )


def _pending_recovery(source_query: str, key: str) -> LegalRewriteDecision:
    return LegalRewriteDecision(
        source_query, None, None, "fallback", "not_called", "interrupted_after_request",
        settings.legal_query_rewrite_model, LEGAL_REWRITE_PROMPT_VERSION,
        legal_rewrite_prompt_hash(), None, None, key, "pending_recovered",
    )


def _cache_pending_recovery(
    source_query: str,
    key: str,
    *,
    final_path: Path,
    pending_path: Path,
) -> LegalRewriteDecision:
    decision = _pending_recovery(source_query, key)
    _atomic_write_json(final_path, asdict(decision))
    pending_path.unlink(missing_ok=True)
    _fsync_directory(final_path.parent)
    return decision


@contextmanager
def legal_rewrite_capture() -> Iterator[None]:
    """Hold the nonblocking process lock for one rewrite-enabled capture.

    The process-wide capture lock complements per-key O_EXCL pending markers and
    prevents a concurrent process from mistaking an active paid request for crash
    residue.
    """
    depth = _CAPTURE_DEPTH.get()
    if depth:
        token = _CAPTURE_DEPTH.set(depth + 1)
        try:
            yield
        finally:
            _CAPTURE_DEPTH.reset(token)
        return

    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".capture.lock"
    lock_handle = lock_path.open("a+b")
    lock_acquired = False
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
        except BlockingIOError as exc:
            raise LegalRewriteCaptureBusy("legal rewrite capture is already active") from exc

        token = _CAPTURE_DEPTH.set(1)
        try:
            yield
        finally:
            _CAPTURE_DEPTH.reset(token)
    finally:
        try:
            if lock_acquired:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def _rewrite_legal_query_locked(source_query: str) -> LegalRewriteDecision:
    if not _CAPTURE_DEPTH.get():
        raise RuntimeError("legal rewrite cache access requires the capture lock")

    cache_dir = _cache_dir()
    key = legal_rewrite_cache_key(source_query)
    final_path = cache_dir / f"{key}.json"
    pending_path = cache_dir / f"{key}.pending.json"
    if final_path.exists():
        cached = _read_cached(
            final_path,
            source_query=source_query,
            cache_key=key,
        )
        if cached is not None:
            return LegalRewriteDecision(**{**asdict(cached), "cache_status": "hit"})
        # A final marker proves that an earlier paid request may have completed.
        # Fall back durably instead of risking a second call for a malformed record.
        return _cache_pending_recovery(
            source_query,
            key,
            final_path=final_path,
            pending_path=pending_path,
        )
    if pending_path.exists():
        return _cache_pending_recovery(
            source_query, key, final_path=final_path, pending_path=pending_path
        )
    if not _write_pending(pending_path, key):
        return _cache_pending_recovery(
            source_query, key, final_path=final_path, pending_path=pending_path
        )

    started = time.perf_counter()
    try:
        raw_output = _call_haiku(render_legal_rewrite_prompt(source_query))
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        decision = _decision_from_parse(
            source_query,
            parse_legal_rewrite_output(
                source_query,
                reconstruct_legal_rewrite_output(raw_output),
            ),
            raw_output_hash=_sha256(raw_output),
            call_latency_ms=latency_ms,
            cache_key=key,
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        decision = _fallback_decision(
            source_query,
            "timeout" if _is_timeout(exc) else "llm_error",
            key,
            call_latency_ms=latency_ms,
        )

    _atomic_write_json(final_path, asdict(decision))
    pending_path.unlink(missing_ok=True)
    _fsync_directory(cache_dir)
    return decision


def rewrite_legal_query(source_query: str, *, enabled: bool = True) -> LegalRewriteDecision:
    """Return one strict cached decision without activating any serving behavior."""
    if not enabled:
        return disabled_legal_rewrite_decision(source_query)
    if _CAPTURE_DEPTH.get():
        return _rewrite_legal_query_locked(source_query)
    with legal_rewrite_capture():
        return _rewrite_legal_query_locked(source_query)
