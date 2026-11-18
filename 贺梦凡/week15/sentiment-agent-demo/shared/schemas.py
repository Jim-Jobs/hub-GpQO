"""API 与进程间传递的数据结构。"""

from enum import Enum

from pydantic import BaseModel, Field


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ChatRequest(BaseModel):
    """前端 → 后端。"""

    text: str = Field(..., min_length=1, description="用户输入文本")


class SentimentResult(BaseModel):
    """进程 1（情感识别）输出。"""

    sentiment: SentimentLabel = Field(..., description="情感标签")
    reason: str = Field(..., description="简要识别依据")


class ChatResponse(BaseModel):
    """后端 → 前端。"""

    text: str = Field(..., description="用户原文")
    sentiment: SentimentLabel = Field(..., description="情感标签")
    reason: str = Field(..., description="识别依据")
    reply: str = Field(..., description="鼓励 / 排解 / 陪伴回应")
