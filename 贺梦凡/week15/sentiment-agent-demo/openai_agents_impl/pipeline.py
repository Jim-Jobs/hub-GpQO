"""后端编排：顺序调用 SentimentAgent → RespondAgent。"""

from __future__ import annotations

from agents import Runner
from agents.run import RunConfig

from openai_agents_impl.agents import get_respond_agent, get_sentiment_agent
from shared.json_utils import parse_model_json
from shared.prompts import build_respond_user_message
from shared.schemas import ChatResponse, SentimentResult


async def run_pipeline(text: str) -> ChatResponse:
    """串联进程 1 与进程 2，返回与 LangGraph 方案相同的 ChatResponse。"""
    run_config = RunConfig(tracing_disabled=True)

    # —— 进程 1：情感识别 ——
    sentiment_run = await Runner.run(
        get_sentiment_agent(),
        text,
        run_config=run_config,
    )
    raw = sentiment_run.final_output
    if isinstance(raw, SentimentResult):
        parsed = raw
    else:
        parsed = parse_model_json(str(raw), SentimentResult)

    # —— 进程 2：按情感生成回应 ——
    respond_input = build_respond_user_message(
        text=text,
        sentiment=parsed.sentiment.value,
        reason=parsed.reason,
    )
    respond_run = await Runner.run(
        get_respond_agent(),
        respond_input,
        run_config=run_config,
    )
    reply = respond_run.final_output
    if not isinstance(reply, str):
        reply = str(reply)

    return ChatResponse(
        text=text,
        sentiment=parsed.sentiment,
        reason=parsed.reason,
        reply=reply.strip(),
    )
