from app.retriever.types import RetrievalResult
from app.retriever.context_builder import build_context
from app.retriever.llm_client import generate, LLMError
from app.config import settings

_GATE_SYSTEM = """You are a gatekeeper for a Philippine-law retrieval system. Decide
whether the passages below contain what is needed to answer the question.

Answer YES if the passages contain the governing legal rule from which the answer
follows directly, even if the question uses ordinary lay wording instead of the
statute's exact words.

Answer NO if the question asks for a current amount, computation, deadline,
registration procedure, filing requirements, or step-by-step process and those
specific details are not present in the passages. Being about the same area of law is
not enough when the asked-for specifics are absent.

Procedure:
1. State in one phrase the specific thing the question asks for.
2. Decide using the two rules above.

Reply in exactly this format:
NEEDS: <the specific thing the question asks for>
ANSWERABLE: YES or NO

Examples:
Question: "Can the police search my house without a warrant?"
Passages: [1987 Constitution Art. III Sec. 2 on unreasonable searches and seizures]
NEEDS: rule on warrantless searches of a dwelling
ANSWERABLE: YES

Question: "Can a Philippine president run for the office again after serving a term?"
Passages: [1987 Constitution Art. VII on the President's term]
NEEDS: presidential re-election / term limit rule
ANSWERABLE: YES

Question: "Is a marriage of a 17-year-old void or merely voidable?"
Passages: [Family Code articles on marriages below the age of majority]
NEEDS: validity of a marriage where a party is under 18
ANSWERABLE: YES

Question: "What is the current daily minimum wage for workers in Metro Manila?"
Passages: [Labor Code provisions on wages, no current wage-order figure]
NEEDS: the current peso minimum-wage amount
ANSWERABLE: NO

Question: "What are the requirements to register a political party with the COMELEC?"
Passages: [Constitution on the party-list system, no registration procedure]
NEEDS: party registration requirements/procedure
ANSWERABLE: NO

Question: "How is the SSS retirement pension benefit computed?"
Passages: [general social-legislation text, no SSS pension formula]
NEEDS: the SSS pension computation formula
ANSWERABLE: NO"""


def _gate_complete(system: str, user: str, model: str) -> str:
    """Run the gate prompt on the configured judge. Anthropic (claude-*) or local
    Ollama; lazy imports so neither dep is required unless its model is selected."""
    if model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        msg = client.messages.create(
            model=model, max_tokens=100, temperature=0,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text
    return generate(system, user, model=model)


def is_answerable(question: str, reranked: list[RetrievalResult]) -> bool:
    """Fail-open answerability gate. Returns True on errors or malformed output;
    only an exact NO triggers abstention."""
    if not reranked:
        return False
    context_block, _ = build_context(reranked)
    user_prompt = f"""Passages:
{context_block}

Question: {question}

NEEDS:"""  # prime the structured field
    try:
        out = _gate_complete(_GATE_SYSTEM, user_prompt, settings.answerability_gate_model)
    except Exception:
        return True  # fail open: a gate outage (Ollama down, API error) must not cause a false abstention
    for line in out.splitlines():
        if line.strip().upper().startswith("ANSWERABLE:"):
            value = line.split(":", 1)[1].strip().upper()
            return value != "NO"
    return True  # unparseable -> fail open
