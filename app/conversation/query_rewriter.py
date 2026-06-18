from app.config import settings
from app.retriever.llm_client import generate
from app.retriever.prompts import REWRITE_PROMPT


def rewrite_query(question: str, history: list[dict]) -> str:
    """Resolve a follow-up into a standalone query using prior turns.

    Returns the original question unchanged when rewriting is disabled or there
    is no history, so the first turn of a session never costs an extra LLM call.
    """
    if not settings.enable_query_rewriting or not history:
        return question
    # Prior QUESTIONS only — never the answer prose. Pronoun/ellipsis resolution
    # needs the earlier questions; feeding full answers lets a weak rewriter fuse
    # the follow-up into stray figures/terms from the answer text (e.g. "bp22"
    # mis-read as the "22,000 pesos" estafa bracket).
    history_block = "\n".join(f"Q: {t['question']}" for t in history)
    if not history_block:
        return question
    prompt = REWRITE_PROMPT.format(history=history_block, question=question)
    rewritten = generate("", prompt).strip()
    return rewritten or question
