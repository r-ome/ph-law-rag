import time

from sentence_transformers import CrossEncoder
from app.retriever.types import RetrievalResult
from app.config import settings
from app.observability.logger import get_logger
from app.retriever.strategy import RetrievalKnobs

logger = get_logger(__name__)

_model: CrossEncoder | None = None
_qwen = None
_bedrock_client = None

# Per-call scoring latency (ms), appended by rerank(). The eval runner reads this to
# report selector cost alongside answer timing — the Qwen3 ship decision is gated on it.
rerank_timings_ms: list[float] = []


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder(settings.reranker_model)
    return _model


class _QwenYesNoReranker:
    """Qwen3-Reranker official scoring path: causal LM, yes/no token logits, softmax → P(yes)."""

    _TASK = "Given a Philippine-law question, retrieve authoritative legal provisions that answer the question."
    _MAX_LENGTH = 8192

    def __init__(self) -> None:
        import torch  # lazy: only the qwen3 backend needs torch/transformers at runtime
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(settings.qwen3_reranker_model, padding_side="left")
        # CPU fp32 measured ~4-5 min/query for a 40-pair pool — unusable. MPS+fp16 is the
        # only viable local path for this backend; plain CPU is kept as a functional fallback.
        if torch.backends.mps.is_available():
            self.model = (
                AutoModelForCausalLM.from_pretrained(settings.qwen3_reranker_model, dtype=torch.float16)
                .to("mps")
                .eval()
            )
        else:
            self.model = AutoModelForCausalLM.from_pretrained(settings.qwen3_reranker_model).eval()
        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
        prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
            'Note that the answer can only be "yes" or "no".<|im_end|>\n'
            "<|im_start|>user\n"
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self.prefix_tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)

    def score(self, query_text: str, texts: list[str]) -> list[float]:
        try:
            return self._score(query_text, texts)
        finally:
            # MPS caching allocator hoards every padded-batch allocation; a 6h eval run
            # grew to 17GB RSS and swap-strangled the box (2026-07-03). Release per call.
            if self.model.device.type == "mps":
                self._torch.mps.empty_cache()

    def _score(self, query_text: str, texts: list[str]) -> list[float]:
        formatted = [
            f"<Instruct>: {self._TASK}\n<Query>: {query_text}\n<Document>: {doc}"
            for doc in texts
        ]
        inputs = self.tokenizer(
            formatted,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=self._MAX_LENGTH - len(self.prefix_tokens) - len(self.suffix_tokens),
        )
        for i, input_ids in enumerate(inputs["input_ids"]):
            inputs["input_ids"][i] = self.prefix_tokens + input_ids + self.suffix_tokens
        padded = self.tokenizer.pad(inputs, padding=True, return_tensors="pt")
        padded = {key: value.to(self.model.device) for key, value in padded.items()}
        with self._torch.no_grad():
            logits = self.model(**padded).logits[:, -1, :]
            true_vector = logits[:, self.token_true_id]
            false_vector = logits[:, self.token_false_id]
            stacked = self._torch.stack([false_vector, true_vector], dim=1)
            scores = self._torch.nn.functional.log_softmax(stacked, dim=1)[:, 1].exp().tolist()
        return [float(s) for s in scores]


def _get_qwen() -> _QwenYesNoReranker:
    global _qwen
    if _qwen is None:
        _qwen = _QwenYesNoReranker()
    return _qwen


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3  # lazy: only the bedrock backend needs boto3 at runtime
        from botocore.config import Config

        # Adaptive client-side rate limiting: the Rerank API throttled back-to-back
        # calls at default retry settings (preflight 2026-07-07, 4 retries exhausted).
        _bedrock_client = boto3.client(
            "bedrock-agent-runtime",
            region_name=settings.bedrock_rerank_region,
            config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
        )
    return _bedrock_client


# amazon.rerank-v1 is quota-capped at 2 requests/MINUTE in this account (non-adjustable;
# verified via service-quotas 2026-07-07). Pace calls to stay under it — botocore's
# adaptive mode assumes per-second limits and exhausts its retries against this quota.
_BEDROCK_MIN_INTERVAL_S = 31.0
_bedrock_last_call = 0.0


def _bedrock_scores(query_text: str, texts: list[str]) -> list[float]:
    """Bedrock Rerank API scores: uncalibrated relevance floats, ordering only."""
    global _bedrock_last_call
    wait = _BEDROCK_MIN_INTERVAL_S - (time.monotonic() - _bedrock_last_call)
    if wait > 0:
        time.sleep(wait)
    _bedrock_last_call = time.monotonic()
    resp = _get_bedrock_client().rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query_text}}],
        sources=[
            {
                "type": "INLINE",
                "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": t}},
            }
            for t in texts
        ],
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {
                    "modelArn": (
                        f"arn:aws:bedrock:{settings.bedrock_rerank_region}"
                        f"::foundation-model/{settings.bedrock_rerank_model}"
                    )
                },
                # A score for every candidate, not just the eventual top-n: the trim
                # below stays backend-agnostic. Pool (~30-40) is under the 100 limit.
                "numberOfResults": len(texts),
            },
        },
    )
    scores = [0.0] * len(texts)
    for item in resp["results"]:
        scores[item["index"]] = float(item["relevanceScore"])
    return scores


def rerank(
    query_text: str,
    results: list[RetrievalResult],
    knobs: RetrievalKnobs | None = None,
) -> list[RetrievalResult]:
    if not results:
        return []
    top_n = knobs.rerank_top_n if knobs else settings.rerank_top_n

    start = time.perf_counter()
    if settings.reranker_backend == "qwen3":
        scores = _get_qwen().score(query_text, [r.text for r in results])
    elif settings.reranker_backend == "bedrock":
        scores = _bedrock_scores(query_text, [r.text for r in results])
    else:
        model = _get_model()
        scores = model.predict([(query_text, r.text) for r in results])
    elapsed_ms = (time.perf_counter() - start) * 1000
    rerank_timings_ms.append(elapsed_ms)

    for r, score in zip(results, scores):
        r.score = float(score)

    results.sort(key=lambda r: r.score, reverse=True)

    if settings.reranker_backend in ("qwen3", "bedrock"):
        # Plain top-8: rerank_score_margin is calibrated to MiniLM logit spread and does
        # not transfer — qwen3 emits [0,1] probabilities, bedrock uncalibrated relevance
        # floats (ordering only; no probability semantics). A native floor per backend
        # is the replacement if trimming proves needed — backlog.
        kept = results[:top_n]
        logger.debug(
            "rerank_completed",
            backend=settings.reranker_backend,
            in_count=len(results),
            out_count=len(kept),
            top_score=kept[0].score if kept else None,
            latency_ms=round(elapsed_ms, 2),
        )
        return kept

    top = results[0].score
    kept = [r for r in results if r.score >= top - settings.rerank_score_margin]
    kept = kept[:top_n]
    logger.debug(
        "rerank_completed",
        backend=settings.reranker_backend,
        in_count=len(results),
        out_count=len(kept),
        top_score=kept[0].score if kept else None,
        latency_ms=round(elapsed_ms, 2),
    )
    return kept
