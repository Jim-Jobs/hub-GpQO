"""跨引擎共享的数据契约。

作业 1 的两套实现（LangGraph / OpenAI Agents SDK）必须使用同一份
``Sentiment`` 与 ``ChatResponse``，否则「对比实验」就没有可比性。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class Sentiment(BaseModel):
    """进程 1 的结构化输出。"""

    label: SentimentLabel = Field(description="情绪分类，只允许 positive / negative / neutral")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0~1")
    reason: str = Field(description="一句话说明判断依据")
    keywords: list[str] = Field(default_factory=list, description="触发判断的关键词")


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户输入")
    engine: Literal["langgraph", "agents"] | None = Field(
        default=None, description="指定编排引擎，缺省使用服务端默认值"
    )


class ChatResponse(BaseModel):
    reply: str
    sentiment: Sentiment
    engine: str
    mode: Literal["live", "mock", "fallback"] = Field(
        description="live=真实模型；mock=无 Key 的离线演示；fallback=模型调用失败后的降级"
    )
    crisis: bool = False
    trail: list[str] = Field(default_factory=list, description="执行路径，便于演示与排查")
    latency_ms: int = 0
    trace_id: str = ""
