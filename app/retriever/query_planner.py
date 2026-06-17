from app.retriever.llm_client import generate, LLMError
from app.config import settings

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

def _plan(question: str) -> list[str]:
    try:
        raw = generate(
            _SYSTEM,
            _USER.format(question=question),
            model=settings.query_planner_model
        )
    except LLMError:
        return [question]

    seen: set[str] = set()
    subs: list[str] = []
    for line in raw.splitlines():
        q = line.strip().lstrip("-*0123456789. ").strip().strip('"\'')
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        subs.append(q)
        if len(subs) >= settings.query_planner_max_subqueries:
            break

    return subs or [question]


def plan_queries(question: str) -> list[str]:
    if not settings.query_decomposition_enabled:
        return [question]
    return _plan(question)