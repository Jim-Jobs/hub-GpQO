"""为 OpenAI Agents SDK 配置兼容接口客户端（如 DeepSeek）。"""

from __future__ import annotations

from openai import AsyncOpenAI

from agents import (
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)

from shared.config import get_api_key, get_base_url, get_model_name

_configured = False


def configure_openai_client() -> str:
    """设置默认 AsyncOpenAI 客户端，返回模型名。"""
    global _configured
    model = get_model_name()
    if _configured:
        return model

    kwargs = {"api_key": get_api_key()}
    base_url = get_base_url()
    if base_url:
        kwargs["base_url"] = base_url

    client = AsyncOpenAI(**kwargs)
    set_default_openai_client(client)
    # 兼容网关多用 Chat Completions
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    _configured = True
    return model
