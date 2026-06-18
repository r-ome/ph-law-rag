from app.retriever.llm_client import generate


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": "answer"}}


def test_generate_sends_deterministic_ollama_options(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr("app.retriever.llm_client.httpx.post", fake_post)

    assert generate("system", "user", model="mistral") == "answer"

    payload = captured["payload"]
    assert "temperature" not in payload
    assert payload["options"] == {"temperature": 0, "seed": 42}
