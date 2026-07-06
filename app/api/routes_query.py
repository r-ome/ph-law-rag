from fastapi import APIRouter
from pydantic import BaseModel
from app.retriever.answer_service import answer
from app.conversation.session import create_session

router = APIRouter(prefix="/query", tags=["query"])

class AskRequest(BaseModel):
    question: str
    session_id: str | None = None
    debug: bool | None = None

@router.post("/ask")
def ask(request: AskRequest):
    session_id = request.session_id or create_session()  # API auto-threads
    result = answer(request.question, debug=request.debug, session_id=session_id, trace_label="api")
    result["session_id"] = session_id
    return result
