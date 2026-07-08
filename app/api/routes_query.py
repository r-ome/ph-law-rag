from fastapi import APIRouter
from pydantic import BaseModel

from app.retriever.answer_service import answer
from app.conversation.session import create_session

router = APIRouter(prefix="/query", tags=["query"])


class Source(BaseModel):
    ref: int
    title: str
    url: str
    source_id: str
    locator: str | None = None
    via: str | None = None


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None
    debug: bool | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    abstained: bool = False
    error: bool = False
    session_id: str


@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    session_id = request.session_id or create_session()  # API auto-threads
    result = answer(request.question, debug=request.debug, session_id=session_id, trace_label="api")
    result["session_id"] = session_id
    return AskResponse(**result)
