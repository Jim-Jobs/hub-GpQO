"""两个版本共用的数据结构。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentimentResult(BaseModel):
    """进程 1 的结构化输出。"""

    sentiment: Sentiment = Field(description="情感类别：正向 / 负向 / 中性")
    confidence: float = Field(
        ge=0.0, le=1.0, description="识别置信度，0 到 1"
    )
    reason: str = Field(description="简短说明识别依据")


class ReplyResult(BaseModel):
    """进程 2 的结果，也是后端返回给前端的最终数据。"""

    sentiment: Sentiment
    strategy: str = Field(description="应对策略：鼓励 / 排解 / 平和回应")
    reply: str = Field(description="给用户的最终回复")
    sentiment_reason: str = Field(default="", description="情感识别依据")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
