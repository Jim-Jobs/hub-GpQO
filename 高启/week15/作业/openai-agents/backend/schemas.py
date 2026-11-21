"""定义 API、Redis 和 SSE 使用的业务数据契约。"""

from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """禁止消息中出现未约定字段的基础模型。"""

    model_config = ConfigDict(extra="forbid")


class Sentiment(str, Enum):
    """情感类别常量，外部值限定为三类字符串。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ChatRequest(StrictModel):
    """前端提交给 API 的请求体。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(
        min_length=1,
        max_length=4000,
        description="用户本轮输入",
    )


class ChatAccepted(StrictModel):
    """API 接受请求后立即返回的订阅信息。"""

    request_id: UUID
    events_url: str


class RedisChatJob(StrictModel):
    """API 写入 Redis 请求流，交给识别进程的任务。"""

    request_id: UUID
    text: str = Field(min_length=1, max_length=4000)


class RedisSentimentResult(StrictModel):
    """识别进程写入 Redis 结果流，交给回复进程的数据。"""

    request_id: UUID
    text: str
    sentiment: Sentiment
    reason: str


class ChatResult(StrictModel):
    """工作流完成后交给前端的最终结果。"""

    request_id: UUID
    sentiment: Sentiment
    reason: str
    reply: str


class WorkflowEventBase(StrictModel):
    """SSE 工作流事件共有的请求编号。"""

    request_id: UUID


class ProgressEvent(WorkflowEventBase):
    """SSE 非终态进度事件。"""

    event_type: Literal["progress"]
    stage: Literal["queued", "sentiment_processing", "reply_processing"]
    message: str


class CompletedEvent(WorkflowEventBase):
    """SSE 成功终态事件。"""

    event_type: Literal["completed"]
    result: ChatResult


class FailureEvent(WorkflowEventBase):
    """SSE 失败终态事件。"""

    event_type: Literal["failed"]
    failed_stage: Literal["sentiment_processing", "reply_processing"]
    error_code: str
    message: str


WorkflowEvent = Annotated[
    ProgressEvent | CompletedEvent | FailureEvent,
    Field(discriminator="event_type"),
]
