"""LangGraph 流程：进程1 情感识别 → 条件边 → 进程2 鼓励/排解。"""
import json
import logging
from typing import Literal

from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph

from app import llm

logger = logging.getLogger(__name__)

SENTIMENT_SYSTEM = (
    "你是情感识别模块，只输出 JSON，不要输出其他内容："
    '{"label": "positive|negative|neutral", "score": 0到1的小数（情感强度）}'
)
ENCOURAGE_SYSTEM = "你是温暖的鼓励师。用户分享了开心、积极的事，用一两句中文真诚地鼓励他，不要空洞套话。"
COMFORT_SYSTEM = "你是温柔的倾听者。用户遇到了烦心事或情绪低落，用两三句中文排解他的情绪：先共情，再给一个具体的宽慰或建议，不要说教。"


class ChatState(TypedDict):
    text: str        # 用户输入
    sentiment: str   # 进程1 输出：positive / negative / neutral
    score: float     # 进程1 输出：情感强度 0~1
    source: str      # 本次走的是大模型还是规则兜底：llm / mock
    mode: str        # 进程2 分支：encourage / comfort
    reply: str       # 进程2 输出


def recognize(state: ChatState) -> dict:
    """进程1：情感识别。"""
    text = state["text"]
    if llm.llm_configured():
        try:
            data = json.loads(llm.chat(SENTIMENT_SYSTEM, text, temperature=0, max_tokens=50))
            label = data.get("label")
            if label not in ("positive", "negative", "neutral"):
                label = "neutral"
            score = float(data.get("score", 0.5))
            if not 0 <= score <= 1:
                score = 0.5
            return {"sentiment": label, "score": score, "source": "llm"}
        except (ValueError, TypeError):
            logger.warning("情感识别输出解析失败，转规则兜底：%s", text[:50])
    label, score = llm.mock_sentiment(text)
    return {"sentiment": label, "score": score, "source": "mock"}


def route(state: ChatState) -> Literal["encourage", "comfort"]:
    """条件边：正向 → 鼓励；负面/中性 → 排解。"""
    return "encourage" if state["sentiment"] == "positive" else "comfort"


def encourage(state: ChatState) -> dict:
    """进程2（正向分支）：鼓励。"""
    if state["source"] == "llm":
        reply = llm.chat(ENCOURAGE_SYSTEM, state["text"])
    else:
        reply = llm.mock_reply(state["text"], "positive")
    return {"mode": "encourage", "reply": reply}


def comfort(state: ChatState) -> dict:
    """进程2（负面/中性分支）：排解。"""
    if state["source"] == "llm":
        reply = llm.chat(COMFORT_SYSTEM, state["text"])
    else:
        reply = llm.mock_reply(state["text"], state["sentiment"])
    return {"mode": "comfort", "reply": reply}


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("recognize", recognize)
    g.add_node("encourage", encourage)
    g.add_node("comfort", comfort)
    g.add_edge(START, "recognize")
    g.add_conditional_edges("recognize", route, {"encourage": "encourage", "comfort": "comfort"})
    g.add_edge("encourage", END)
    g.add_edge("comfort", END)
    return g.compile()


graph = build_graph()
