import time

import httpx

from app.config import settings
from app.observability.context import record_stage
from app.observability.logger import get_logger

logger = get_logger(__name__)

class LLMError(Exception):
    """Raised when the LLM call fails or returns an unexpected response."""


def _strip_reasoning(text: str) -> str:
    """Drop chain-of-thought from reasoning models (qwen3, deepseek-r1).

    Keeps only the text after the final </think>, so it works whether or not
    the model emits a matching opening <think> tag. Non-reasoning models pass
    through untouched.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


def generate(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """Generate a completion. Routes by model name: claude* → Anthropic, else Ollama.

    This is the single generator seam. Swapping the generator for an A/B is a
    config change (LLM_MODEL=claude-haiku-4-5) — retriever, judge, and embeddings
    are untouched. The eval runner labels output files by settings.llm_model, so
    the run is self-labeling.
    """
    model = model or settings.llm_model
    prompt_length = len(system_prompt or "") + len(user_prompt or "")
    start = time.perf_counter()
    succeeded = False
    try:
        if model.startswith("claude"):
            result = _generate_anthropic(system_prompt, user_prompt, model)
        else:
            result = _generate_ollama(system_prompt, user_prompt, model, max_tokens=max_tokens)
        succeeded = True
        return result
    except LLMError as exc:
        logger.warning("llm_generate_failed", model=model, prompt_length=prompt_length, error=str(exc))
        record_stage(
            "llm_generate",
            ms=(time.perf_counter() - start) * 1000,
            model=model,
            prompt_length=prompt_length,
            error=str(exc),
        )
        raise
    finally:
        if succeeded:
            record_stage(
                "llm_generate",
                ms=(time.perf_counter() - start) * 1000,
                model=model,
                prompt_length=prompt_length,
            )


def _generate_ollama(
    system_prompt: str, user_prompt: str, model: str, max_tokens: int | None = None
) -> str:
    url = f"{settings.ollama_base_url}/api/chat"
    options = {"temperature": 0, "seed": 42}
    if max_tokens is not None:
        options["num_predict"] = max_tokens  # cap local output so the gate verdict isn't truncated
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "think": False,
        "options": options,
    }

    try:
        resp = httpx.post(url, json=payload, timeout=120)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise LLMError(
            f"Ollama returned {e.response.status_code}:{e.response.text}"
        ) from e
    except httpx.RequestError as e:
        raise LLMError(
            f"Could not reach Ollama at {settings.ollama_base_url}: {e}"
        ) from e

    data = resp.json()
    try:
        return _strip_reasoning(data["message"]["content"])
    except(KeyError, TypeError) as e:
        raise LLMError(f"Unexpected Ollama response shape: {data}") from e


def _generate_anthropic(system_prompt: str, user_prompt: str, model: str) -> str:
    import anthropic  # lazy: optional backend, keep CLI startup independent of it

    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        raise LLMError("anthropic_api_key is not set (needed for claude generator)")
    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,  # Haiku 4.5 still accepts sampling params (greedy for determinism)
            system=system_prompt or "",
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.APIError as e:
        raise LLMError(f"Anthropic API error: {e}") from e

    text = "".join(b.text for b in resp.content if b.type == "text")
    return _strip_reasoning(text)
