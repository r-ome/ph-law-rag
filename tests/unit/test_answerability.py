import sys
import types

import pytest

from app.retriever import answerability

pytestmark = pytest.mark.unit


class _FakeText:
    text = "ANSWERABLE: YES"


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(content=[_FakeText()])


def test_gate_complete_uses_default_and_override_token_budgets(monkeypatch):
    fake_messages = _FakeMessages()

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = fake_messages

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=FakeAnthropic),
    )

    answerability._gate_complete("system", "user", "claude-haiku-4-5")
    assert fake_messages.kwargs["max_tokens"] == 100

    answerability._gate_complete(
        "system",
        "user",
        "claude-haiku-4-5",
        max_tokens=512,
    )
    assert fake_messages.kwargs["max_tokens"] == 512
