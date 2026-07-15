from app.observability.logger import get_logger
from app.retriever.llm_client import generate, LLMError
from app.config import settings

logger = get_logger(__name__)

_SYSTEM = (
    "You rewrite a user's Philippine-law question into search queries for retrieval.\n"
    "Your job is to improve recall, not answer the question.\n"
    "\n"
    "Rules:\n"
    "- If the input is not a legal question (a greeting, small talk, empty, or "
    "otherwise has no legal issue to research), return it unchanged as a single line. "
    "Do NOT invent a legal question.\n"
    "- Output 1 to 3 queries, one per line.\n"
    "- If the question asks one legal issue, return it unchanged as a single line.\n"
    "- Split only when the question clearly contains multiple facets, such as "
    "different provisions, elements, remedies, exceptions, time periods, or comparisons.\n"
    "- Each query must be standalone and preserve the user's key legal terms.\n"
    "- Preserve exact citations exactly as written, including RA numbers, article numbers, "
    "section numbers, and G.R. numbers.\n"
    "- Do not invent statutes, citations, doctrines, facts, parties, or issues not present "
    "in the question.\n"
    "- Do not answer the question.\n"
    "- Output ONLY the queries. No numbering, bullets, labels, explanations, or commentary."
)

_USER = "Question:\n{question}\n\nQueries:"

def _plan(
    question: str,
    *,
    model: str | None = None,
    max_subqueries: int | None = None,
) -> list[str]:
    model = model or settings.query_planner_model
    max_subqueries = max_subqueries or settings.query_planner_max_subqueries
    try:
        raw = generate(
            _SYSTEM,
            _USER.format(question=question),
            model=model,
        )
    except LLMError as exc:
        logger.warning("query_decomposition_failed", model=model, error=str(exc))
        return [question]

    seen: set[str] = set()
    subs: list[str] = []
    for line in raw.splitlines():
        q = line.strip().lstrip("-*0123456789. ").strip().strip('"\'')
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        subs.append(q)
        if len(subs) >= max_subqueries:
            break

    result = subs or [question]
    logger.info(
        "query_decomposed",
        model=model,
        question=question,
        subqueries=result,
        split=len(result) > 1 or result[0] != question,
    )
    return result


def plan_queries(question: str, knobs=None) -> list[str]:
    enabled = (
        knobs.query_decomposition_enabled if knobs is not None
        else settings.query_decomposition_enabled
    )
    if not enabled:
        return [question]
    if knobs is not None:
        return _plan(
            question,
            model=knobs.query_planner_model,
            max_subqueries=knobs.query_planner_max_subqueries,
        )
    return _plan(question)
