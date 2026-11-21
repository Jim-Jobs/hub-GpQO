"""模型工厂：一个 ChatOpenAI 实例，供 LangGraph 引擎与真实模式复用。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import Settings, settings


def build_chat_model(cfg: Settings = settings) -> ChatOpenAI | None:
    if cfg.mode != "live":
        return None
    return ChatOpenAI(
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=0.4,
        timeout=cfg.timeout,
        max_retries=2,
    )
