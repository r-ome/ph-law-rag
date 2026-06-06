# from app.retriever.hybrid_retriever import hybrid_retriever
# from app.retriever.reranker import rerank

from app.retriever.llm_client import generate

def retrieve(query_text: str):
    # hits = hybrid_retriever(query_text)
    # top = rerank(query_text, hits)
    # for r in top:
    #     print(round(r.score, 3), r.metadata.get("source_id"), r.text[:80])
        
    # return "query"
    
    return generate("You are concise", query_text)