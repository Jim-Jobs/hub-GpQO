"""
契约层（Contract Layer）—— 后端 B：OpenAI-Agents 服务

本文件只放数据结构定义：
  - ChatRequest / ChatResponse：前后端 HTTP 通信契约（与 langgraph_service 完全一致）
  - SentimentOutput：分类 Agent 的结构化输出类型（output_type）
"""

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """POST /chat 的请求体。"""

    user_input: str = Field(..., description="用户输入的文本")


class ChatResponse(BaseModel):
    """POST /chat 的响应体。"""

    sentiment: Literal["positive", "negative"] = Field(..., description="情感二分类结果")
    response: str = Field(..., description="AI 生成的鼓励 / 排解话术")


# ---------------------------------------------------------------------------
# Agent 结构化输出
# ---------------------------------------------------------------------------
class SentimentOutput(BaseModel):
    """
    sentiment_agent 的结构化输出模型。

    Agents SDK 会把它转换成 JSON Schema 约束模型输出，
    拿到手的 final_output 就是这个类的实例。
    """

    sentiment: Literal["positive", "negative"] = Field(
        ..., description="用户文本的情感倾向：positive 或 negative"
    )
