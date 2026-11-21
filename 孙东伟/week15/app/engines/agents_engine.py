"""作业 1 的 OpenAI Agents SDK 实现。

设计取舍：这里没有用 ``handoff`` 做路由，而是「进程 1 结构化输出 → Python 分支
→ 进程 2」。原因是 handoff 的语义是「把对话控制权交给另一个 Agent」，路由由模型
自行决定，不可测也不可控；而作业要的是确定性的两段式流水线。

流程：
    Runner.run(sentiment_agent)  →  Sentiment
        ├─ positive → Runner.run(encourager)
        ├─ negative → Runner.run(comforter)
        ├─ neutral  → Runner.run(neutral_agent)
        └─ crisis   → Runner.run(crisis_agent)
"""

from __future__ import annotations

import os

from agents import Agent, Runner, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from app.engines.base import Engine, EngineResult
from app.mock import MockBrain, detect_crisis
from app.prompts import (
    COMFORT_PROMPT,
    CRISIS_PROMPT,
    ENCOURAGE_PROMPT,
    NEUTRAL_PROMPT,
    SENTIMENT_PROMPT,
)
from app.schemas import Sentiment, SentimentLabel


class AgentsEngine(Engine):
    name = "agents"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
        brain: MockBrain | None = None,
    ) -> None:
        self.brain = brain
        # 指向兼容端点时不要往 OpenAI 官方 tracing 上传数据。
        if brain is not None or "api.openai.com" not in base_url:
            set_tracing_disabled(True)
        os.environ.setdefault("OPENAI_API_KEY", api_key or "sk-placeholder")

        self._client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=base_url, timeout=timeout)

        def chat_model() -> OpenAIChatCompletionsModel:
            return OpenAIChatCompletionsModel(model=model, openai_client=self._client)

        self.sentiment_agent = Agent(
            name="sentiment_analyzer",
            instructions=SENTIMENT_PROMPT,
            output_type=Sentiment,
            model=chat_model(),
        )
        self.encourager = Agent(
            name="encourager", instructions=ENCOURAGE_PROMPT, model=chat_model()
        )
        self.comforter = Agent(
            name="comforter", instructions=COMFORT_PROMPT, model=chat_model()
        )
        self.neutral_agent = Agent(
            name="neutral_responder", instructions=NEUTRAL_PROMPT, model=chat_model()
        )
        self.crisis_agent = Agent(
            name="crisis_responder", instructions=CRISIS_PROMPT, model=chat_model()
        )

    def _pick(self, label: SentimentLabel) -> Agent:
        return {
            SentimentLabel.POSITIVE: self.encourager,
            SentimentLabel.NEGATIVE: self.comforter,
        }.get(label, self.neutral_agent)

    async def run(self, text: str) -> EngineResult:
        return await self._safe_run(text, self._run)

    async def _run(self, text: str) -> EngineResult:
        crisis = detect_crisis(text)
        if self.brain is not None:
            sentiment = self.brain.analyze(text)
        else:
            first = await Runner.run(self.sentiment_agent, text)
            sentiment = first.final_output_as(Sentiment)

        if crisis:
            agent, node_name = self.crisis_agent, "crisis"
        else:
            agent, node_name = self._pick(sentiment.label), self._pick(sentiment.label).name

        if self.brain is not None:
            reply = self.brain.reply(text, sentiment, crisis=crisis)
        else:
            prompt = (
                f"用户原话：{text}\n"
                f"情绪判断：{sentiment.label.value}\n"
                f"判断依据：{sentiment.reason}"
            )
            second = await Runner.run(agent, prompt)
            reply = str(second.final_output)

        return EngineResult(
            sentiment=sentiment,
            reply=reply.strip(),
            trail=["sentiment_analyzer", node_name],
            mode="mock" if self.brain is not None else "live",
            crisis=crisis,
        )
