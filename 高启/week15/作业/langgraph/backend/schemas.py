"""LangGraph 项目的 API、Redis 和 SSE 业务数据契约。"""

from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Sentiment(str, Enum):
    """情感识别统一标签。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentimentAgentOutput(BaseModel):
    """情感识别 Agent 必须返回的结构化字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sentiment: Sentiment
    reason: str = Field(min_length=1, max_length=500)


class ChatRequest(BaseModel):
    """前端提交的单轮聊天文本。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4000, description="用户本轮输入")


class ChatAccepted(BaseModel):
    """API 接受任务后立即返回的订阅信息。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(description="本次请求唯一编号")
    events_url: str = Field(description="SSE 进度订阅路径")


class RedisChatJob(BaseModel):
    """API 写入请求流并交给情感识别进程的消息。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    text: str = Field(min_length=1, max_length=4000)


class RedisSentimentResult(BaseModel):
    """识别进程写入情感结果流并交给回复进程的消息。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    text: str
    sentiment: Sentiment
    reason: str = Field(description="简短识别依据")


class ChatResult(BaseModel):
    """最终返回给前端的情感识别和回复结果。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    sentiment: Sentiment
    reason: str
    reply: str


class WorkflowEventBase(BaseModel):
    """所有 SSE 事件共有的请求关联字段。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class ProgressEvent(WorkflowEventBase):
    """前端展示的非终态工作进度事件。"""

    event_type: Literal["progress"]
    stage: Literal["queued", "sentiment_processing", "reply_processing"]
    message: str


class CompletedEvent(WorkflowEventBase):
    """请求成功完成时发送的终态事件。"""

    event_type: Literal["completed"]
    result: ChatResult


class FailureEvent(WorkflowEventBase):
    """请求失败时发送的终态事件。"""

    event_type: Literal["failed"]
    failed_stage: Literal["sentiment_processing", "reply_processing"]
    error_code: str
    message: str


WorkflowEvent = Annotated[
    ProgressEvent | CompletedEvent | FailureEvent,
    Field(discriminator="event_type"),
]
