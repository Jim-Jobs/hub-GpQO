"""后端进程：接收前端输入，串联进程1（情感识别）与进程2（鼓励/排解）。"""
import logging

from agents import Runner

from app import llm
from app.agents_def import (
    SentimentResult,
    comfort_agent,
    encourage_agent,
    parse_sentiment,
    sentiment_agent,
)

logger = logging.getLogger(__name__)


async def run(text: str) -> dict:
    """串联两个进程，返回 {sentiment, score, mode, reply, source}。"""
    # ---- 进程1：情感识别 ----
    sentiment: SentimentResult | None = None
    source = "mock"
    if llm.llm_configured():
        result = await Runner.run(sentiment_agent, text)
        sentiment = parse_sentiment(str(result.final_output))
        if sentiment is not None:
            source = "llm"
    if sentiment is None:
        label, score = llm.mock_sentiment(text)
        sentiment = SentimentResult(label=label, score=score)  # type: ignore[arg-type]

    # ---- 进程2：正向鼓励，负面/中性排解 ----
    positive = sentiment.label == "positive"
    agent = encourage_agent if positive else comfort_agent
    if source == "llm":
        result = await Runner.run(agent, text)
        reply = str(result.final_output)
    else:
        reply = llm.mock_reply(text, sentiment.label)

    return {
        "sentiment": sentiment.label,
        "score": sentiment.score,
        "mode": "encourage" if positive else "comfort",
        "reply": reply,
        "source": source,
    }
