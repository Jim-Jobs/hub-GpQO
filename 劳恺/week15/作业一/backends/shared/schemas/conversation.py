"""编排器对外的会话与聊天协议。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backends.shared.constants.emotions import EmotionCategory
from backends.shared.constants.strategies import ResponseStrategy


class ChatRequest(BaseModel):
    """``POST /api/chat/stream`` 请求体。``conversation_id`` 为空则新建会话。"""

    conversation_id: str | None = None
    message: str = Field(min_length=1)


class MessageRecord(BaseModel):
    """一条持久化消息。"""

    id: int
    role: str
    content: str
    emotion_category: EmotionCategory | None = None
    confidence: float | None = None
    emotion_type: str | None = None
    response_strategy: ResponseStrategy | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    """会话列表项。"""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationDetail(ConversationSummary):
    """会话详情，含完整消息历史。"""

    messages: list[MessageRecord] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    """``POST /api/conversations`` 请求体。"""

    title: str | None = None
