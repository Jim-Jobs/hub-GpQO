"""进程间 HTTP 契约（pydantic schema）。

两套实现（openai-agents / langgraph）共享同一份定义、各自复制一份：
改动契约时两边同步修改，保证进程1/2 可交叉替换（README 演示点）。

契约概览（见 ../design.md §4）：
  进程1 POST /analyze   AnalyzeRequest  -> SentimentResult
  进程2 POST /respond   RespondRequest  -> SSE 流（delta/done，不走本文件模型）
  网关 POST /api/chat   ChatRequest     -> SSE 流（meta/sentiment/delta/done/error）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: 情感三分类；进程1 输出、进程2 路由、网关透传共用
SentimentLabel = Literal["positive", "negative", "neutral"]

#: 会话历史中的角色（system 不跨进程传递）
HistoryRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """会话历史中的一条消息；进程1/2 均无状态，history 由网关每轮携带。"""

    role: HistoryRole
    content: str = Field(min_length=1, max_length=8000)


class AnalyzeRequest(BaseModel):
    """进程1 请求体：当前输入 + 多轮历史。"""

    text: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)


class SentimentResult(BaseModel):
    """进程1 响应体：三分类标签 + 一句话中文依据。

    同时作为 openai-agents `Agent(output_type=...)` 与 langgraph
    `with_structured_output(...)` 的结构化输出 schema。
    """

    label: SentimentLabel
    reason: str = Field(min_length=1, max_length=200)


class RespondRequest(BaseModel):
    """进程2 请求体：用户文本 + 识别结果 + 历史；响应为 SSE 流。"""

    text: str = Field(min_length=1, max_length=2000)
    label: SentimentLabel
    reason: str = Field(default="", max_length=200)
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)


class ChatRequest(BaseModel):
    """网关请求体（前端 -> 网关）：用户输入 + 可选续用会话。"""

    text: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None
