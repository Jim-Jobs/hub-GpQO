"""两套编排实现共享的抽象。

作业要求「后端进程串联进程 1 和进程 2」。这里的 ``Engine`` 就是那层串联逻辑：
无论内部用图还是用 Agent，对外都只有 ``run(text)`` 一个入口，
这样 FastAPI 才能在 LangGraph 与 Agents SDK 之间无缝切换。
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field

from app.schemas import Sentiment

logger = logging.getLogger(__name__)


@dataclass
class EngineResult:
    sentiment: Sentiment
    reply: str
    trail: list[str] = field(default_factory=list)
    mode: str = "live"
    crisis: bool = False


class Engine(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def run(self, text: str) -> EngineResult:
        """进程 1（识别）+ 进程 2（回应）。"""
        raise NotImplementedError

    async def _safe_run(self, text: str, impl) -> EngineResult:
        """真实模型失败时降级到离线引擎，保证演示不中断。"""
        try:
            return await impl(text)
        except Exception as exc:  # noqa: BLE001 - 需要兜住所有供应商异常
            logger.exception("[%s] 模型调用失败，降级为 mock: %s", self.name, exc)
            from app.mock import MockBrain

            brain = MockBrain()
            sentiment = brain.analyze(text)
            return EngineResult(
                sentiment=sentiment,
                reply=brain.reply(text, sentiment),
                trail=["analyze", "mock_reply"],
                mode="fallback",
            )
