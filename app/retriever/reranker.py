from sentence_transformers import CrossEncoder
from app.retriever.types import RetrievalResult
from app.config import settings

_model: CrossEncoder | None = None

def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder(settings.reranker_model)
    return _model

def rerank(query_text:str, results: list[RetrievalResult]) -> list[RetrievalResult]:
    if not results:
        return []
    
    model = _get_model()
    pairs = [(query_text, r.text) for r in results]
    scores = model.predict(pairs)
    
    for r, score in zip(results, scores):
        r.score = float(score)
        
    results.sort(key=lambda r: r.score, reverse=True)
    return results[: settings.rerank_top_n]
