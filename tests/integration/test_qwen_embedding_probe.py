import pytest

from app.config import settings
from app.indexing.embedder import get_embed_model
from app.retriever.dense_retriever import _format_embedding_query


def test_qwen_query_instruction_reaches_embedding_model(monkeypatch):
    monkeypatch.setattr(settings, "embedding_backend", "ollama")
    monkeypatch.setattr(settings, "embedding_model", "qwen3-embedding:0.6b")
    monkeypatch.setattr(settings, "embedding_dim", 1024)
    monkeypatch.setattr(
        settings,
        "embedding_query_instruction",
        "Given a Philippine law question, retrieve the statutory provisions and jurisprudence that answer it.",
    )

    try:
        embed_model = get_embed_model()
        text = "What is the prescriptive period for libel?"
        query_vector = embed_model.get_query_embedding(_format_embedding_query(text))
        doc_vector = embed_model.get_text_embedding(text)
    except Exception as exc:
        pytest.skip(f"Ollama qwen3-embedding:0.6b unavailable: {exc}")

    if len(query_vector) != 1024 or len(doc_vector) != 1024:
        pytest.skip(
            "Ollama qwen3-embedding:0.6b returned unexpected dimensions: "
            f"query={len(query_vector)}, doc={len(doc_vector)}"
        )

    assert query_vector != doc_vector
