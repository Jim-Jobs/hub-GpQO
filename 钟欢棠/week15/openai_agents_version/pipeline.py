"""openai-agents 版：进程 1 情感识别 Agent → 进程 2 回应 Agent。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents import Agent, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from common.config import settings
from common.mock_llm import mock_recognize, mock_reply
from common.schemas import ReplyResult, Sentiment, SentimentResult

RECOGNIZE_PROMPT = (
    "你是情感识别器。判断用户输入是正向、负向还是中性，"
    "输出结构化结果：sentiment、confidence(0~1) 和 reason。"
    "只依据用户文字判断，不要生成回应。"
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

# 进程 1：结构化输出情感识别结果
sentiment_agent = Agent(
    name="情感识别员",
    instructions=RECOGNIZE_PROMPT,
    output_type=SentimentResult,
)

# 进程 2：三个由路由选择的回应 Agent
encourage_agent = Agent(name="鼓励师", instructions=ENCOURAGE_PROMPT)
comfort_agent = Agent(name="排解师", instructions=COMFORT_PROMPT)
neutral_agent = Agent(name="平和回应者", instructions=NEUTRAL_PROMPT)


def _run_config() -> RunConfig:
    cfg = settings()
    client = AsyncOpenAI(
        api_key=cfg.api_key or "sk-placeholder-mock",
        base_url=cfg.base_url or None,
    )
    model = OpenAIChatCompletionsModel(model=cfg.model, openai_client=client)
    return RunConfig(model=model, tracing_disabled=True)


def _pick_agent(sentiment: Sentiment) -> tuple[Agent, str]:
    if sentiment is Sentiment.POSITIVE:
        return encourage_agent, "鼓励"
    if sentiment is Sentiment.NEGATIVE:
        return comfort_agent, "排解"
    return neutral_agent, "平和回应"


async def run_pipeline(user_text: str) -> ReplyResult:
    """依次运行进程 1 与进程 2。"""
    cfg = settings()

    if cfg.mock:
        sentiment_result = mock_recognize(user_text)
        agent, strategy = _pick_agent(sentiment_result.sentiment)
        reply = mock_reply(sentiment_result.sentiment, user_text)
        return ReplyResult(
            sentiment=sentiment_result.sentiment,
            strategy=strategy,
            reply=reply,
            sentiment_reason=sentiment_result.reason,
            confidence=sentiment_result.confidence,
        )

    run_config = _run_config()

    # 进程 1
    first = await Runner.run(sentiment_agent, user_text, run_config=run_config)
    sentiment_result: SentimentResult = first.final_output
    if not isinstance(sentiment_result, SentimentResult):
        sentiment_result = SentimentResult.model_validate(sentiment_result)

    # 进程 2：把识别结果作为上下文交给对应 Agent
    agent, strategy = _pick_agent(sentiment_result.sentiment)
    second = await Runner.run(
        agent,
        (
            f"用户原话：{user_text}\n"
            f"进程 1 情感识别结果：{sentiment_result.model_dump()}\n"
            "请按你的角色直接给出回应。"
        ),
        run_config=run_config,
    )

    return ReplyResult(
        sentiment=sentiment_result.sentiment,
        strategy=strategy,
        reply=str(second.final_output),
        sentiment_reason=sentiment_result.reason,
        confidence=sentiment_result.confidence,
    )
