"""
契约层（Contract Layer）—— 后端 A：LangGraph 服务

本文件只放数据结构定义，不包含任何业务逻辑：
  - ChatRequest / ChatResponse：前后端 HTTP 通信契约（必须与 agents_service 完全一致）
  - AgentState：LangGraph 状态图中在各节点之间流转的状态
"""

from typing import Literal, TypedDict

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
# 图状态
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    """
    LangGraph 全局状态。

    total=False 表示字段都是可选的：
      - 识别节点只返回 {"sentiment": ...}
      - 生成节点只返回 {"response": ...}
    这样各个节点可以只更新自己负责的字段，由 LangGraph 自动合并。
    """

    user_input: str  # 入口传入
    sentiment: Literal["positive", "negative"]  # 进程 1（识别）产出
    response: str  # 进程 2（生成）产出
