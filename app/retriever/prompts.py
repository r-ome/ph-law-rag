ABSTAIN_PREFIX = "I don't have enough information in the indexed Philippine law corpus"
ABSTAIN_MESSAGE = f"{ABSTAIN_PREFIX} to answer that question."

def is_abstention(answer_text: str) -> bool:
      """True only for a genuine full refusal. The prompt instructs the model to
      emit the boilerplate refusal phrase ALONE — nothing before it — when it
      cannot answer, so a strict leading-prefix check is reliable across models.
      A partial answer (which leads with substantive text and may note the
      uncovered remainder afterwards) does not start with the phrase, so it is
      correctly treated as an answer, not a refusal."""
      return answer_text.strip().startswith(ABSTAIN_PREFIX)

GREETING_MESSAGE = (
    "Hello! I'm a Philippine law research assistant. Ask me about Philippine "
    "statutes, the Constitution, or case law — for example: \"What are the "
    "grounds for annulment of marriage?\""
)

# Whole-message greetings / chitchat that are not legal questions. Matched only
# when the ENTIRE message (minus surrounding punctuation) equals one of these, so
# a real question that merely opens with "hello" is NOT short-circuited.
_CONVERSATIONAL = {
    "hi", "hello", "hey", "heya", "hiya", "yo", "sup", "howdy",
    "good morning", "good afternoon", "good evening", "good day",
    "thanks", "thank you", "thank you so much", "ty", "thx",
    "ok", "okay", "cool", "nice", "great",
    "test", "testing", "ping",
    "who are you", "what are you", "what can you do", "what do you do",
    "help", "hello there",
}

def is_conversational(text: str) -> bool:
    """True for a greeting / chitchat that isn't a legal question.

    Conservative on purpose: only fires when the whole message is a known
    conversational phrase (or empty), so short legal queries like 'estafa?' are
    never caught. A greeting that prefixes a real question (e.g. 'hi, what is
    estafa?') does not match and flows to normal retrieval."""
    t = text.strip().lower().strip("!.?,;:- ")
    return not t or t in _CONVERSATIONAL

SYSTEM_PROMPT = """You are a legal research assistant for Philippine law. \
You answer strictly from the numbered context passages provided for you.

Rule:
- use ONLY the information in the provided context. Do not rely on outside \
    knowledge, prior training or assumptions about Philippine law.
- Cite every claim with the reference number of the passage it comes from, \
    in square brackets, e.g. [1] or [2][3]. Place the citation right after the claim.
- The square-bracket citation contains ONLY the reference number from the context (e.g. [1], [4]). \
    When you mention an article or section number, write it as plain text (e.g. 'Article 1489'), never in brackets.
- If the context does not contain enough information to answer the question at all, \
    your ENTIRE reply must be exactly this sentence, with NO text before or after it: {abstain_message} \
    Do not explain why, do not describe what the passages discuss — output only that one sentence.
- Do not invent article numbers, section numbers, Republic Act numbers, or \
    case citations. Only cite identifiers that appear in the context.
- Be concise and precise. Quote the operative legal text when it matters. \
- Do NOT add steps, procedures, requirements, deadlines, penalties, exceptions, or consequences
    unless they are stated word-for-word in the context. If the context describes a rule but not its
    procedure, state only the rule and stop. Do not "complete" or "explain further" from general legal
    knowledge. \
- If the context answers only part of the question, answer that part and explicitly say the
    remainder is not covered by the indexed corpus. Do not fill the gap from outside knowledge. \
- Before answering, check each sentence: if it is not directly supported by a cited passage, delete it.
""".format(abstain_message=ABSTAIN_MESSAGE)

REWRITE_PROMPT = """You rewrite a follow-up question into a standalone question using the conversation history. Do not answer it — only rewrite it.

Rules:
- Resolve ONLY references to earlier turns: pronouns (it, that, those, them) and ellipsis (e.g. "what about section 5?", "and the penalty?").
- Preserve every specific term in the follow-up EXACTLY as written — names, abbreviations, acronyms, statute numbers, and figures. Never reinterpret or expand them. ("BP22" is the name of a law, not an amount of money.)
- If the follow-up names a NEW topic, treat it as a topic change: rewrite it as a standalone question about that new topic. Do NOT merge it with the previous topic.
- Output only the rewritten question. If it is already standalone, return it unchanged.

Example 1
History:
Q: What are the grounds for annulment of marriage?
Follow-up: what is the prescriptive period for those?
Standalone question: What is the prescriptive period for the grounds for annulment of marriage?

Example 2
History:
Q: What is estafa?
Follow-up: how about bp22?
Standalone question: What is BP 22?

Example 3
History:
Q: What is estafa?
Follow-up: What are the elements of theft?
Standalone question: What are the elements of theft?

Now rewrite.
History:
{history}
Follow-up: {question}
Standalone question:"""

def build_user_prompt(question: str, context_block: str)-> str:
    return f"""Context passages:
{context_block}

Question: {question}

Answer (grounded with the context above, with [n] citations):"""
