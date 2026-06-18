import httpx

from app.config import settings

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



def generate(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": model or settings.llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "seed": 42,
        },
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
