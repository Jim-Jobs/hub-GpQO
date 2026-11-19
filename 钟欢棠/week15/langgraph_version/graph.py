"""LangGraph 版：进程 1 情感识别 → 进程 2 按结果鼓励 / 排解。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, TypedDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from common.config import settings
from common.mock_llm import mock_recognize, mock_reply
from common.schemas import ReplyResult, Sentiment, SentimentResult


class GraphState(TypedDict):
    user_text: str
    sentiment: SentimentResult | None
    sentiment_reason: str
    confidence: float
    strategy: str
    reply: str


RECOGNIZE_PROMPT = (
    "你是情感识别器。判断用户文字的情感倾向，只输出结构化结果："
    "positive（正向）、negative（负向）或 neutral（中性），"
    "并给出 0~1 的置信度和简短理由。不要输出其他内容。"
)
ENCOURAGE_PROMPT = (
    "你是温暖的鼓励者。用户情感为正向，请结合他的原话真诚鼓励，"
    "肯定其努力与收获，并提出继续前进的小建议。用中文，100 字以内。"
)
COMFORT_PROMPT = (
    "你是温和的倾听者。用户情感为负向，请先共情和接纳他的情绪，"
    "再给出可操作的排解建议，不评判、不说教。用中文，120 字以内。"
)
NEUTRAL_PROMPT = (
    "你是友善的回应者。用户情感为中性，请平和回应并主动邀请他分享更多。"
    "用中文，80 字以内。"
)


def _chat_model() -> ChatOpenAI:
    cfg = settings()
    return ChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key or "sk-placeholder-mock",
        base_url=cfg.base_url or None,
        temperature=0.3,
    )


def recognize_sentiment(state: GraphState) -> dict:
    """进程 1：调用模型输出结构化的情感识别结果。"""
    cfg = settings()
    if cfg.mock:
        result = mock_recognize(state["user_text"])
    else:
        result = _chat_model().with_structured_output(SentimentResult).invoke(
            [
                SystemMessage(content=RECOGNIZE_PROMPT),
                HumanMessage(content=state["user_text"]),
            ]
        )
    return {
        "sentiment": result,
        "sentiment_reason": result.reason,
        "confidence": result.confidence,
    }


def route_response(
    state: GraphState,
) -> Literal["encourage", "comfort", "neutral"]:
    """进程 2 的路由：按进程 1 的结果决定走哪条回应分支。"""
    sentiment = state["sentiment"]
    assert sentiment is not None
    if sentiment.sentiment is Sentiment.POSITIVE:
        return "encourage"
    if sentiment.sentiment is Sentiment.NEGATIVE:
        return "comfort"
    return "neutral"


def _respond(state: GraphState, prompt: str, strategy: str) -> dict:
    """进程 2：根据识别结果生成鼓励 / 排解 / 平和回应。"""
    cfg = settings()
    if cfg.mock:
        assert state["sentiment"] is not None
        reply = mock_reply(state["sentiment"].sentiment, state["user_text"])
    else:
        reply = _chat_model().invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"用户原话：{state['user_text']}\n"
                        f"情感识别结果：{state['sentiment'].model_dump()}"
                    )
                ),
            ]
        ).content
        reply = str(reply)
    return {"strategy": strategy, "reply": reply}


def encourage_node(state: GraphState) -> dict:
    return _respond(state, ENCOURAGE_PROMPT, "鼓励")


def comfort_node(state: GraphState) -> dict:
    return _respond(state, COMFORT_PROMPT, "排解")


def neutral_node(state: GraphState) -> dict:
    return _respond(state, NEUTRAL_PROMPT, "平和回应")


def build_graph():
    """编译并返回一个 LangGraph 工作流。"""
    builder = StateGraph(GraphState)
    builder.add_node("recognize_sentiment", recognize_sentiment)
    builder.add_node("encourage", encourage_node)
    builder.add_node("comfort", comfort_node)
    builder.add_node("neutral", neutral_node)

    builder.add_edge(START, "recognize_sentiment")
    builder.add_conditional_edges(
        "recognize_sentiment",
        route_response,
        {"encourage": "encourage", "comfort": "comfort", "neutral": "neutral"},
    )
    for node in ("encourage", "comfort", "neutral"):
        builder.add_edge(node, END)
    return builder.compile()


graph = build_graph()


async def run_pipeline(user_text: str) -> ReplyResult:
    """后端调用的入口：输入用户文本，返回完整结果。"""
    state = await graph.ainvoke(
        {
            "user_text": user_text,
            "sentiment": None,
            "sentiment_reason": "",
            "confidence": 0.0,
            "strategy": "",
            "reply": "",
        }
    )
    sentiment: SentimentResult = state["sentiment"]
    return ReplyResult(
        sentiment=sentiment.sentiment,
        strategy=state["strategy"],
        reply=state["reply"],
        sentiment_reason=state["sentiment_reason"],
        confidence=state["confidence"],
    )
