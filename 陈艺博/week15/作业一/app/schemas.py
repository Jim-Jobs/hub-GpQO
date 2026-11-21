from typing import Literal

from pydantic import BaseModel, Field


Emotion = Literal["positive", "negative", "neutral"]


class EmotionResult(BaseModel):
    emotion: Emotion = Field(description="文本情感：positive、negative 或 neutral")
    reason: str = Field(description="简短的判断依据")


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="用户输入")


class ChatResponse(BaseModel):
    framework: Literal["langgraph", "agents"]
    emotion: Emotion
    reason: str
    reply: str
