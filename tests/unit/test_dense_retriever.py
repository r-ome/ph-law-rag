from app.config import settings
from app.retriever.dense_retriever import _format_embedding_query


def test_format_embedding_query_adds_instruction(monkeypatch):
    instruction = "Retrieve relevant Philippine law."
    query = "What is qualified theft?"
    monkeypatch.setattr(settings, "embedding_query_instruction", instruction)

    assert _format_embedding_query(query) == f"Instruct: {instruction}\nQuery: {query}"


def test_format_embedding_query_no_instruction_is_identity(monkeypatch):
    query = "What is qualified theft?"

    monkeypatch.setattr(settings, "embedding_query_instruction", None)
    assert _format_embedding_query(query) == query

    monkeypatch.setattr(settings, "embedding_query_instruction", "")
    assert _format_embedding_query(query) == query
