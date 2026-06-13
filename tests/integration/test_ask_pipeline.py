import pytest
from pathlib import Path

from app.config import settings
from app.retriever.answer_service import answer

def _service_ready() -> bool:
    from app.api.health_query import ping_url
    from app.db import list_documents
    
    if not ping_url(f"{settings.qdrant_url}/collections"):
        return False
    if not ping_url(f"{settings.ollama_base_url}/api/version"):
        return False
    if not Path(settings.bm25_path).exists():
        return False
    
    try:
        return bool(list_documents())
    except Exception:
        return False
    
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _service_ready(),
        reason="needs live Qdrant + Ollama and an indexed corpus (run `raglab sync`)"
    )
]

def test_in_corpus_questions_returns_grounded_answer():
    query = "What are the requisites of a valid contract under the Civil Code?"
    resp = answer(query)
    
    assert resp["error"] is False
    assert resp["abstained"] is False
    assert resp["sources"], "expected at least one cited source"
    assert isinstance(resp["answer"], str) and resp["answer"].strip()
    
def test_out_of_scope_question_abstains():
    # A genuinely out-of-scope legal question (no tax law in the corpus) is a
    # stabler, more meaningful abstention test than nonsense input.
    query = "What is the income tax rate for individual taxpayers in the Philippines?"
    resp = answer(query)
    
    assert resp["abstained"] is True
    assert resp["sources"] == []
    
def test_debug_mode_exposes_retrieval_trace():
    query = "What are the requisites of a valid contract under the Civil Code?"
    resp = answer(query, debug=True)
    
    assert "debug" in resp
    trace = resp["debug"]
    assert "num_retrieved" in trace
    assert "num_reranked" in trace
    assert "chunks" in trace