from fastapi import APIRouter
from pydantic import BaseModel
from app.retriever.answer_service import answer

router = APIRouter(prefix="/query", tags=["query"])

class AskRequest(BaseModel):
    question: str
    debug: bool | None = None

@router.post("/ask")
def ask(request: AskRequest):
	return answer(request.question, debug=request.debug)
