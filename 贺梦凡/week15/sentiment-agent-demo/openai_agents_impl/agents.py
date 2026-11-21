"""进程 1 / 进程 2 对应的两个 Agent。"""

from __future__ import annotations

from agents import Agent

from openai_agents_impl.client_setup import configure_openai_client
from shared.prompts import RESPOND_SYSTEM, SENTIMENT_SYSTEM

SENTIMENT_JSON_HINT = (
    '\n请严格输出 JSON 对象，格式：'
    '{"sentiment":"positive|negative|neutral","reason":"一句话依据"}'
    "，不要输出其它文字。"
)


def build_sentiment_agent() -> Agent:
    """进程 1：情感识别。

    使用 JSON 文本输出而非 output_type，以兼容 DeepSeek 等网关。
    """
    model = configure_openai_client()
    return Agent(
        name="SentimentAgent",
        instructions=SENTIMENT_SYSTEM + SENTIMENT_JSON_HINT,
        model=model,
    )


def build_respond_agent() -> Agent:
    """进程 2：鼓励 / 排解 / 陪伴。"""
    model = configure_openai_client()
    return Agent(
        name="RespondAgent",
        instructions=RESPOND_SYSTEM,
        model=model,
    )


_sentiment_agent: Agent | None = None
_respond_agent: Agent | None = None


def get_sentiment_agent() -> Agent:
    global _sentiment_agent
    if _sentiment_agent is None:
        _sentiment_agent = build_sentiment_agent()
    return _sentiment_agent


def get_respond_agent() -> Agent:
    global _respond_agent
    if _respond_agent is None:
        _respond_agent = build_respond_agent()
    return _respond_agent
