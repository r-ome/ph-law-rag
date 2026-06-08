from ragas import evaluate, EvaluationDataset, RunConfig
from ragas.metrics import (
    Faithfulness,
    ResponseRelevancy,
    LLMContextPrecisionWithReference,
    LLMContextRecall
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.config import settings

def score(results: list[dict]):
    run_config = RunConfig(timeout=600, max_workers=2)
    scorable = [r for r in results if not r["abstained"] and r["contexts"]]
    if not scorable:
        return None, []

    samples = [
        {
            "user_input": r["question"],
            "response": r["answer"],
            "retrieved_contexts": r["contexts"],
            "reference": r["ground_truth"],
        }
        for r in scorable
    ]
    dataset = EvaluationDataset.from_list(samples)

    llm = LangchainLLMWrapper(ChatOllama(
        model=settings.ragas_llm_model,
        base_url=settings.ollama_base_url,
        temperature=0
    ))
    embeddings = LangchainEmbeddingsWrapper(OllamaEmbeddings(
        model=settings.ragas_embedding_model,
        base_url=settings.ollama_base_url
    ))

    result = evaluate(
        dataset=dataset,
        metrics=[
            Faithfulness(),
            ResponseRelevancy(),
            LLMContextPrecisionWithReference(),
            LLMContextRecall()
        ],
        llm=llm,
        embeddings=embeddings,
        run_config=run_config
    )
    return result, scorable
