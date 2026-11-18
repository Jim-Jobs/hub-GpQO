"""进程 1 / 进程 2 对应的 Graph 节点。"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage

from langgraph_impl.llm import get_chat_model
from shared.json_utils import parse_model_json
from shared.prompts import (
    RESPOND_SYSTEM,
    SENTIMENT_SYSTEM,
    build_respond_user_message,
)
from shared.schemas import SentimentResult

SENTIMENT_JSON_HINT = (
    '\n请严格输出 JSON 对象，格式：'
    '{"sentiment":"positive|negative|neutral","reason":"一句话依据"}'
    "，不要输出其它文字。"
)


class GraphState(TypedDict, total=False):
    """图状态：在节点间传递。"""

    text: str
    sentiment: str
    reason: str
    reply: str


def sentiment_node(state: GraphState) -> GraphState:
    """进程 1：情感识别。"""
    model = get_chat_model()
    messages = [
        SystemMessage(content=SENTIMENT_SYSTEM + SENTIMENT_JSON_HINT),
        HumanMessage(content=state["text"]),
    ]
    # 优先 structured_output；兼容网关不支持时回退 JSON 文本解析
    try:
        structured = model.with_structured_output(SentimentResult)
        result = structured.invoke(messages)
        if not isinstance(result, SentimentResult):
            raise TypeError("structured_output 未返回 SentimentResult")
    except (TypeError, ValueError, KeyError):
        ai_message = model.invoke(messages)
        content = ai_message.content
        text = content if isinstance(content, str) else str(content)
        result = parse_model_json(text, SentimentResult)
    except Exception as exc:
        # 其它错误（含鉴权/网络）：若像 schema 不兼容，再尝试 JSON 回退
        name = type(exc).__name__
        if "Authentication" in name or "RateLimit" in name or "APIConnection" in name:
            raise
        ai_message = model.invoke(messages)
        content = ai_message.content
        text = content if isinstance(content, str) else str(content)
        result = parse_model_json(text, SentimentResult)

    return {
        "sentiment": result.sentiment.value,
        "reason": result.reason,
    }


def respond_node(state: GraphState) -> GraphState:
    """进程 2：按情感生成鼓励 / 排解 / 陪伴回应。"""
    model = get_chat_model()
    message = build_respond_user_message(
        text=state["text"],
        sentiment=state["sentiment"],
        reason=state["reason"],
    )
    ai_message = model.invoke(
        [
            SystemMessage(content=RESPOND_SYSTEM),
            HumanMessage(content=message),
        ]
    )
    content = ai_message.content
    reply = content if isinstance(content, str) else str(content)
    return {"reply": reply.strip()}
