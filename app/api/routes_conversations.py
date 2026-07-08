from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_query import Source
from app.conversation.session import get_conversation, list_conversations

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    session_id: str
    title: str
    created_at: str
    turn_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class ConversationTurn(BaseModel):
    turn_index: int
    question: str
    answer: str
    sources: list[Source] = []


class ConversationDetail(BaseModel):
    session_id: str
    turn_count: int
    turns: list[ConversationTurn]


@router.get("", response_model=ConversationListResponse, summary="List conversations")
def conversations() -> ConversationListResponse:
    return ConversationListResponse(conversations=list_conversations())


@router.get(
    "/{session_id}",
    response_model=ConversationDetail,
    summary="Full conversation with per-turn citations",
)
def conversation_detail(session_id: str) -> ConversationDetail:
    detail = get_conversation(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetail(**detail)
