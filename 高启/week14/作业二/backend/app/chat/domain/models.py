"""问答输入、响应及可轮询状态的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.core.serialization import from_json_values, to_json


@dataclass(frozen=True)
class ChatImage:
    """问答请求或模型输入中的图像字节及其 MIME 类型。"""

    data: bytes
    media_type: str


@dataclass(frozen=True)
class ChatRequest:
    """单次问答输入；可选图片只在任务执行期间保留于内存。"""

    question_text: str
    image: bytes | None = None
    image_media_type: str | None = None


@dataclass(frozen=True)
class ChatResponse:
    """问答最终响应，按顺序保存回答片段、引用和受控图片资源。"""

    route: str
    answer_segments: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    image_assets: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        """生成保存或传输问答结果用的 JSON 字符串。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> ChatResponse:
        """从 JSON 恢复问答结果。"""
        return cls(**from_json_values(payload))


@dataclass(frozen=True)
class ChatStatusRecord:
    """供 Redis 持久化并由前端轮询的问答状态记录。"""

    chat_id: str
    status: ChatStatus
    stage: ChatStage
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    response: ChatResponse | None = None
    error: str | None = None

    def to_json(self) -> str:
        """生成可写入 Redis 的问答状态 JSON。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> ChatStatusRecord:
        """从 Redis JSON 恢复状态、阶段、时间及嵌套响应。"""
        values = from_json_values(payload)
        values["status"] = ChatStatus(values["status"])
        values["stage"] = ChatStage(values["stage"])
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        if values.get("response") is not None:
            values["response"] = ChatResponse(**values["response"])
        return cls(**values)


@dataclass(frozen=True)
class ChatExecutionEvent:
    """不含用户内容的问答阶段事件，用于本地 JSONL 运行日志。"""

    chat_id: str
    stage: ChatStage
    event_type: str
    model_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int | None = None
    retrieved_count: int = 0
    error_code: str | None = None
