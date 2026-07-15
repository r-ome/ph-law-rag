import json
import sys
import types

import pytest

from app.config import settings
from app.retriever import legal_query_rewriter as rewriter


pytestmark = pytest.mark.unit


SOURCE = "What protection does RA 9262 provide?"
HIGH_OUTPUT = json.dumps(
    {
        "legal_query": SOURCE + " | Legal terms: Anti-Violence Against Women and Their Children Act protective remedies",
        "citations": [],
        "confidence": "high",
    },
    separators=(",", ":"),
)
HIGH_CONTINUATION = HIGH_OUTPUT[len(rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL) :]


@pytest.fixture(autouse=True)
def rewrite_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "legal_query_rewrite_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "legal_query_rewrite_model", "claude-haiku-4-5")


@pytest.mark.parametrize(
    ("raw", "outcome"),
    [
        (HIGH_OUTPUT, "valid"),
        ("```json\n" + HIGH_OUTPUT + "\n```", "invalid"),
        ("Here: " + HIGH_OUTPUT, "invalid"),
        (json.dumps({"legal_query": "x", "citations": [], "confidence": "high", "extra": 1}), "invalid"),
        (json.dumps({"legal_query": SOURCE + " | Legal terms: law", "citations": ["RA 1"], "confidence": "high"}), "invalid"),
        (json.dumps({"legal_query": "changed | Legal terms: law", "citations": [], "confidence": "high"}), "literal_violation"),
        (json.dumps({"legal_query": SOURCE + " | Legal terms:  law", "citations": [], "confidence": "high"}), "literal_violation"),
        (json.dumps({"legal_query": SOURCE + " | Legal terms: Section 99 requirements", "citations": [], "confidence": "high"}), "literal_violation"),
        (json.dumps({"legal_query": SOURCE + " | Legal terms: Republic Act No. 9999 requirements", "citations": [], "confidence": "high"}), "literal_violation"),
        (json.dumps({"legal_query": SOURCE + " | Legal terms: The answer is file a case", "citations": [], "confidence": "high"}), "literal_violation"),
    ],
)
def test_strict_parser_cases(raw, outcome):
    parsed = rewriter.parse_legal_rewrite_output(SOURCE, raw)
    assert parsed.outcome == outcome


def test_parser_preserves_original_literal_and_allows_existing_identifier():
    output = json.dumps(
        {
            "legal_query": SOURCE + " | Legal terms: RA 9262 protection order legal requirements",
            "citations": [],
            "confidence": "high",
        },
        separators=(",", ":"),
    )
    parsed = rewriter.parse_legal_rewrite_output(SOURCE, output)
    assert parsed.outcome == "valid"
    assert parsed.legal_query == json.loads(output)["legal_query"]

    legitimate_or = json.dumps(
        {
            "legal_query": SOURCE + " | Legal terms: civil or criminal protective remedies",
            "citations": [],
            "confidence": "high",
        },
        separators=(",", ":"),
    )
    assert rewriter.parse_legal_rewrite_output(SOURCE, legitimate_or).outcome == "valid"


@pytest.mark.parametrize(
    "legal_suffix",
    [
        "Republic Act No. 9262 protection order legal requirements",
        "RA No. 9262 protection order legal requirements",
    ],
)
def test_parser_allows_no_abbreviation_for_existing_identifier(legal_suffix):
    output = json.dumps(
        {
            "legal_query": SOURCE + " | Legal terms: " + legal_suffix,
            "citations": [],
            "confidence": "high",
        },
        separators=(",", ":"),
    )

    parsed = rewriter.parse_legal_rewrite_output(SOURCE, output)

    assert parsed.outcome == "valid"
    assert parsed.legal_query == json.loads(output)["legal_query"]


@pytest.mark.parametrize("answer_prose", ["No, it does not apply", "Yes, it does apply"])
def test_parser_rejects_yes_or_no_answer_prose(answer_prose):
    output = json.dumps(
        {
            "legal_query": SOURCE + " | Legal terms: " + answer_prose,
            "citations": [],
            "confidence": "high",
        },
        separators=(",", ":"),
    )

    assert rewriter.parse_legal_rewrite_output(SOURCE, output).outcome == "literal_violation"


def test_citation_injection_is_rejected():
    output = json.dumps(
        {
            "legal_query": SOURCE + " | Legal terms: RA 9262 protection order",
            "citations": ["https://example.test"],
            "confidence": "high",
        }
    )
    assert rewriter.parse_legal_rewrite_output(SOURCE, output).outcome == "invalid"


def test_prompt_v3_pins_doctrine_only_instruction_and_contract_identity():
    assert rewriter.LEGAL_REWRITE_PROMPT_VERSION == "v3"
    assert (
        '`confidence` (a JSON string whose value is exactly `"high"` or `"low"`; never a number)'
        in rewriter.LEGAL_REWRITE_SYSTEM_PROMPT
    )
    assert (
        "Never mention any statute number, act number, article number, section "
        "number, or case or docket number in the rendering. Describe the doctrine "
        "or legal concept in words instead."
        in rewriter.LEGAL_REWRITE_SYSTEM_PROMPT
    )
    prompt_hash = rewriter.legal_rewrite_prompt_hash()
    assert prompt_hash == (
        "a4ce4cd52e55e5ca23d532106bb5ce0532cb0bd4631cbda52ffc16120dcc2a91"
    )
    assert prompt_hash != (
        "1b1ebdcc28f8e4840c8ba209e59be7aea5ec36fc28f9629dfde8a6dc584f5a82"
    )
    assert prompt_hash != (
        "0737db82638fa3624b591cfbf006a372dc74e147dcf0594683c7ae3c902ee598"
    )


def test_call_uses_exact_anthropic_parameters(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.messages = self

        def create(self, **kwargs):
            captured["create"] = kwargs
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text=HIGH_CONTINUATION)]
            )

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    assert rewriter._call_haiku("rendered") == HIGH_CONTINUATION
    assert captured["init"]["timeout"] == 15.0
    assert captured["init"]["max_retries"] == 0
    assert captured["create"] == {
        "model": "claude-haiku-4-5",
        "max_tokens": 160,
        "temperature": 0,
        "system": rewriter.LEGAL_REWRITE_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": "rendered"},
            {
                "role": "assistant",
                "content": rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL,
            },
        ],
    }


def test_prefill_and_reconstruction_rule_are_pinned_in_prompt_identity(monkeypatch):
    baseline = rewriter.legal_rewrite_prompt_hash()
    assert rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL == '{"legal_query":"'
    assert (
        rewriter.reconstruct_legal_rewrite_output(HIGH_CONTINUATION) == HIGH_OUTPUT
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(rewriter, "LEGAL_REWRITE_ASSISTANT_PREFILL", "different")
        assert rewriter.legal_rewrite_prompt_hash() != baseline
    with monkeypatch.context() as scoped:
        scoped.setattr(
            rewriter,
            "LEGAL_REWRITE_RESPONSE_RECONSTRUCTION",
            "different reconstruction",
        )
        assert rewriter.legal_rewrite_prompt_hash() != baseline


def test_timeout_api_and_low_confidence_fallbacks_are_cached(monkeypatch):
    cases = [
        (TimeoutError("slow"), "timeout"),
        (RuntimeError("api unavailable"), "llm_error"),
        ("low", "low_confidence"),
        ("not-json", "invalid_output"),
        ("literal", "literal_violation"),
    ]
    for index, (response, reason) in enumerate(cases):
        query = f"{SOURCE} ({index})"
        calls = []

        def fake_call(_prompt, response=response):
            calls.append(True)
            if isinstance(response, Exception):
                raise response
            if response == "low":
                full_output = json.dumps(
                    {
                        "legal_query": query + " | Legal terms: RA 9262 remedies",
                        "citations": [],
                        "confidence": "low",
                    },
                    separators=(",", ":"),
                )
                return full_output[len(rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL) :]
            if response == "literal":
                full_output = json.dumps(
                    {
                        "legal_query": query + " | Legal terms: Section 999 rule",
                        "citations": [],
                        "confidence": "high",
                    },
                    separators=(",", ":"),
                )
                return full_output[len(rewriter.LEGAL_REWRITE_ASSISTANT_PREFILL) :]
            return response

        monkeypatch.setattr(rewriter, "_call_haiku", fake_call)
        first = rewriter.rewrite_legal_query(query)
        second = rewriter.rewrite_legal_query(query)
        assert first.status == "fallback"
        assert first.fallback_reason == reason
        assert first.cache_status == "miss_written"
        assert second.cache_status == "hit"
        assert second.fallback_reason == reason
        assert calls == [True]
        if reason in {"timeout", "llm_error"}:
            assert first.call_latency_ms is not None


def test_cache_hit_makes_no_paid_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rewriter,
        "_call_haiku",
        lambda _prompt: calls.append(True) or HIGH_CONTINUATION,
    )
    first = rewriter.rewrite_legal_query(SOURCE)
    second = rewriter.rewrite_legal_query(SOURCE)
    assert first.status == "accepted"
    assert second.status == "accepted"
    assert second.cache_status == "hit"
    assert calls == [True]


def test_pending_recovery_makes_no_paid_call(monkeypatch):
    key = rewriter.legal_rewrite_cache_key(SOURCE)
    cache_dir = rewriter._cache_dir()
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.pending.json").write_text('{"state":"pending"}', encoding="utf-8")
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: pytest.fail("paid call retried"))
    decision = rewriter.rewrite_legal_query(SOURCE)
    assert decision.fallback_reason == "interrupted_after_request"
    assert decision.cache_status == "pending_recovered"
    assert rewriter.rewrite_legal_query(SOURCE).cache_status == "hit"


def test_malformed_final_record_falls_back_without_a_paid_call(monkeypatch):
    key = rewriter.legal_rewrite_cache_key(SOURCE)
    cache_dir = rewriter._cache_dir()
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({"source_query": "tampered"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: pytest.fail("paid call retried"))

    decision = rewriter.rewrite_legal_query(SOURCE)

    assert decision.fallback_reason == "interrupted_after_request"
    assert decision.cache_status == "pending_recovered"
    assert rewriter.rewrite_legal_query(SOURCE).cache_status == "hit"


def test_lock_contention_rejects_before_key_artifacts_or_request(monkeypatch):
    called = []
    monkeypatch.setattr(
        rewriter,
        "_call_haiku",
        lambda _prompt: called.append(True) or HIGH_CONTINUATION,
    )
    monkeypatch.setattr(rewriter.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    with pytest.raises(rewriter.LegalRewriteCaptureBusy):
        rewriter.rewrite_legal_query(SOURCE)
    cache_dir = rewriter._cache_dir()
    assert not list(cache_dir.glob("*.json"))
    assert called == []


def test_lock_releases_after_normal_exit_and_crash_simulation(monkeypatch):
    calls = []

    def tracking_flock(_fd, operation):
        calls.append(operation)

    monkeypatch.setattr(rewriter.fcntl, "flock", tracking_flock)
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: HIGH_CONTINUATION)
    rewriter.rewrite_legal_query(SOURCE)
    unlocks_after_normal = calls.count(rewriter.fcntl.LOCK_UN)
    assert unlocks_after_normal == 1

    crash_query = SOURCE + " crash"
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rewriter.rewrite_legal_query(crash_query)
    assert calls.count(rewriter.fcntl.LOCK_UN) == unlocks_after_normal + 1

    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: pytest.fail("crash residue retried"))
    recovered = rewriter.rewrite_legal_query(crash_query)
    assert recovered.cache_status == "pending_recovered"


def test_capture_lock_is_held_once_across_multiple_rewrites(monkeypatch):
    operations = []
    monkeypatch.setattr(
        rewriter.fcntl,
        "flock",
        lambda _fd, operation: operations.append(operation),
    )
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: HIGH_CONTINUATION)

    with rewriter.legal_rewrite_capture():
        rewriter.rewrite_legal_query(SOURCE)
        second_source = SOURCE + " second"
        monkeypatch.setattr(
            rewriter,
            "_call_haiku",
            lambda _prompt: HIGH_CONTINUATION.replace(SOURCE, second_source, 1),
        )
        rewriter.rewrite_legal_query(second_source)

    exclusive = rewriter.fcntl.LOCK_EX | rewriter.fcntl.LOCK_NB
    assert operations == [exclusive, rewriter.fcntl.LOCK_UN]


def test_cache_key_changes_with_prompt_version_hash_model_and_query(monkeypatch):
    baseline = rewriter.legal_rewrite_cache_key(SOURCE)
    monkeypatch.setattr(rewriter, "LEGAL_REWRITE_PROMPT_VERSION", "v4")
    assert rewriter.legal_rewrite_cache_key(SOURCE) != baseline
    monkeypatch.undo()
    monkeypatch.setattr(rewriter, "legal_rewrite_prompt_hash", lambda: "different")
    assert rewriter.legal_rewrite_cache_key(SOURCE) != baseline
    monkeypatch.undo()
    monkeypatch.setattr(settings, "legal_query_rewrite_model", "other-model")
    assert rewriter.legal_rewrite_cache_key(SOURCE) != baseline
    assert rewriter.legal_rewrite_cache_key(SOURCE + "!") != baseline


@pytest.mark.parametrize(
    ("historical_prompt_version", "historical_prompt_hash"),
    [
        (
            "v1",
            "1b1ebdcc28f8e4840c8ba209e59be7aea5ec36fc28f9629dfde8a6dc584f5a82",
        ),
        (
            "v2",
            "0737db82638fa3624b591cfbf006a372dc74e147dcf0594683c7ae3c902ee598",
        ),
    ],
)
def test_real_historical_cache_key_cannot_hit_v3(
    monkeypatch, historical_prompt_version, historical_prompt_hash
):
    historical_key = rewriter._sha256(
        {
            "contract_version": 1,
            "prompt_version": historical_prompt_version,
            "prompt_hash": historical_prompt_hash,
            "model": settings.legal_query_rewrite_model,
            "max_tokens": 160,
            "source_query": SOURCE,
        }
    )

    v3_key = rewriter.legal_rewrite_cache_key(SOURCE)
    assert v3_key != historical_key
    cache_dir = rewriter._cache_dir()
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{historical_key}.json").write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        rewriter,
        "_call_haiku",
        lambda _prompt: calls.append(True) or HIGH_CONTINUATION,
    )

    decision = rewriter.rewrite_legal_query(SOURCE)

    assert decision.cache_key == v3_key
    assert decision.cache_status == "miss_written"
    assert decision.status == "accepted"
    assert calls == [True]


def test_raw_output_is_persisted_only_as_a_hash(monkeypatch):
    raw = HIGH_CONTINUATION + "-secret-output"
    monkeypatch.setattr(rewriter, "_call_haiku", lambda _prompt: raw)
    decision = rewriter.rewrite_legal_query(SOURCE)
    cache_file = rewriter._cache_dir() / f"{decision.cache_key}.json"
    persisted = cache_file.read_text(encoding="utf-8")
    assert raw not in persisted
    assert decision.raw_output_hash == rewriter._sha256(raw)
    assert "raw_output" not in json.loads(persisted)
