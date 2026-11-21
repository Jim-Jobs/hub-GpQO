from typing import Literal

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="用户输入")


class ChatOut(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"]
    score: float = Field(ge=0, le=1, description="情感强度")
    mode: Literal["encourage", "comfort"]
    reply: str
    source: Literal["llm", "mock"] = Field(description="本次回答来源：大模型 / 规则兜底")


class HealthOut(BaseModel):
    status: Literal["ok"]
    engine: Literal["langgraph"]
    llm_configured: bool
