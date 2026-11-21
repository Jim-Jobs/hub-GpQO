"""对外契约：请求、情感识别结果、第二段回复、完整响应。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SentimentLabel = Literal["positive", "negative", "neutral"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="用户输入文本")
    session_id: str | None = Field(default=None, description="可选会话标识，便于前端串联多轮")


class SentimentResult(BaseModel):
    """进程 1 的输出，同时作为结构化输出 schema 交给模型。"""

    label: SentimentLabel = Field(description="情感倾向：positive / negative / neutral")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0~1")
    intensity: int = Field(ge=1, le=5, description="情绪强度 1~5")
    emotions: list[str] = Field(default_factory=list, description="最多 3 个具体情绪词")
    reason: str = Field(description="40 字以内的判断依据")


class SupportReply(BaseModel):
    """进程 2 的输出结构。"""

    reply: str = Field(description="给用户的回复正文")
    tips: list[str] = Field(default_factory=list, description="2~3 条一句话行动建议")


class StageTrace(BaseModel):
    """每一步的执行轨迹，前端会以时间轴形式展示。"""

    stage: str
    name: str
    elapsed_ms: int
    detail: str


class ChatResponse(BaseModel):
    message: str
    sentiment: SentimentResult
    reply: str
    tone: Literal["encourage", "relief", "neutral"]
    tips: list[str]
    elapsed_ms: int
    trace: list[StageTrace]
