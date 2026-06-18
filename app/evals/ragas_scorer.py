from langchain_anthropic import ChatAnthropic
from ragas import evaluate, EvaluationDataset, RunConfig
from ragas.metrics import (
    Faithfulness,
    ResponseRelevancy,
    LLMContextPrecisionWithReference,
    LLMContextRecall
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_ollama import OllamaEmbeddings

from app.config import settings
from app.evals import ragas_cache


class CachedEvaluationResult:
    def __init__(self, samples: list[dict], scores: list[dict]):
        self.samples = samples
        self.scores = scores

    def to_pandas(self, batch_size: int | None = None, batched: bool = False):
        import pandas as pd

        return pd.concat(
            [pd.DataFrame(self.samples), pd.DataFrame(self.scores)],
            axis=1,
        )


def _evaluate_samples(samples: list[dict]):
    dataset = EvaluationDataset.from_list(samples)

    llm = LangchainLLMWrapper(ChatAnthropic(
        model_name=settings.ragas_llm_model,
        timeout=120,
        max_tokens_to_sample=4096,
        max_retries=3,
        stop=None,
        temperature=0,
        api_key=settings.anthropic_api_key
    ))
    embeddings = LangchainEmbeddingsWrapper(OllamaEmbeddings(
        model=settings.ragas_embedding_model,
        base_url=settings.ollama_base_url
    ))

    return evaluate(
        dataset=dataset,
        metrics=[
            Faithfulness(),
            ResponseRelevancy(),
            LLMContextPrecisionWithReference(),
            LLMContextRecall()
        ],
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(timeout=600, max_workers=3, max_retries=15, max_wait=90)
    )


def score(results: list[dict], use_cache: bool = True):
    scorable = [r for r in results if not r["abstained"] and r["contexts"]]
    if not scorable:
        return None, []

    samples = [ragas_cache.sample_from_result(r) for r in scorable]

    if not use_cache:
        result = _evaluate_samples(samples)
        return result, scorable

    keys = [ragas_cache.cache_key(sample) for sample in samples]
    cached = ragas_cache.get_many(keys)
    combined_scores: list[dict | None] = [cached.get(key) for key in keys]
    miss_positions = [i for i, score_row in enumerate(combined_scores) if score_row is None]

    print(
        "\nRAGAS score cache: "
        f"{len(samples) - len(miss_positions)} hits, "
        f"{len(miss_positions)} misses, "
        f"{len(miss_positions)} costed rows"
    )

    if miss_positions:
        miss_samples = [samples[i] for i in miss_positions]
        miss_result = _evaluate_samples(miss_samples)
        new_cache_rows = []
        for offset, scores in enumerate(miss_result.scores):
            pos = miss_positions[offset]
            combined_scores[pos] = scores
            new_cache_rows.append((keys[pos], samples[pos], scores))
        ragas_cache.put_many(new_cache_rows)

    result = CachedEvaluationResult(
        samples=samples,
        scores=[row for row in combined_scores if row is not None],
    )
    return result, scorable
