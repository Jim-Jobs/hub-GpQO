"""会话管理 API（两套框架协议完全一致）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from starlette.requests import Request

from backends.shared.db.session import Database
from backends.shared.schemas.conversation import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    MessageRecord,
)


def _database(request: Request) -> Database:
    return request.app.state.database


def build_conversations_router() -> APIRouter:
    router = APIRouter(prefix="/api/conversations", tags=["conversations"])

    @router.get("", response_model=list[ConversationSummary])
    def list_conversations(request: Request) -> list[ConversationSummary]:
        rows = _database(request).list_conversations()
        return [
            ConversationSummary(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=count,
            )
            for conversation, count in rows
        ]

    @router.post("", response_model=ConversationDetail, status_code=201)
    def create_conversation(
        request: Request, body: CreateConversationRequest | None = None
    ) -> ConversationDetail:
        database = _database(request)
        title = body.title if body else None
        conversation = database.create_conversation(title)
        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=0,
            messages=[],
        )

    @router.get("/{conversation_id}", response_model=ConversationDetail)
    def get_conversation(conversation_id: str, request: Request) -> ConversationDetail:
        database = _database(request)
        conversation = database.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = database.get_messages(conversation_id)
        return ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=len(messages),
            messages=[
                MessageRecord(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    emotion_category=message.emotion_category,
                    confidence=message.confidence,
                    emotion_type=message.emotion_type,
                    response_strategy=message.response_strategy,
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )

    @router.delete("/{conversation_id}")
    def delete_conversation(conversation_id: str, request: Request) -> Response:
        deleted = _database(request).delete_conversation(conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
        return Response(status_code=204)

    return router
