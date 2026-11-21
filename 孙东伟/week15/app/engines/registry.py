"""引擎注册表：根据配置装配 LangGraph / Agents SDK 两套实现。"""

from __future__ import annotations

from app.config import Settings, settings
from app.engines.agents_engine import AgentsEngine
from app.engines.base import Engine
from app.engines.langgraph_engine import LangGraphEngine
from app.llm import build_chat_model
from app.mock import MockBrain


def build_engines(cfg: Settings = settings) -> dict[str, Engine]:
    if cfg.mode == "mock":
        brain = MockBrain()
        return {
            "langgraph": LangGraphEngine(brain=brain),
            "agents": AgentsEngine(brain=brain),
        }

    model = build_chat_model(cfg)
    return {
        "langgraph": LangGraphEngine(model=model),
        "agents": AgentsEngine(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            timeout=cfg.timeout,
        ),
    }
