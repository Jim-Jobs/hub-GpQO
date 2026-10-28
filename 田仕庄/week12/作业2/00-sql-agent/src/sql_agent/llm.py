"""LangChain 模型工厂：统一构造 ChatOpenAI，并复用连接池。

延迟构造（lru_cache）是刻意的：`ChatOpenAI(...)` 在构造期就校验 API key，
无 key 时直接抛 `OpenAIError`。如果像原来 openai-agents 那样在模块级建对象，
整套离线测试会在 import 阶段就炸掉。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.output_parsers import PydanticToolsParser
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import settings


def _require_api_key() -> str:
    key = settings.api_key
    if not key:
        raise RuntimeError(
            "缺少 OPENAI_API_KEY（或 DEEPSEEK_API_KEY）。\n"
            "请先在环境变量或 .env 中设置，例如：\n"
            "  OPENAI_API_KEY=sk-...\n"
            f"  OPENAI_BASE_URL={settings.base_url}  # 可选"
        )
    return key


@lru_cache(maxsize=8)
def _build(model: str, temperature: float) -> ChatOpenAI:
    extra_body: dict[str, Any] = {}
    # auto = 不传该参数，沿用服务端默认（deepseek-flash 默认开启思考模式）
    if settings.thinking_mode != "auto":
        extra_body["thinking"] = {"type": settings.thinking_mode}

    return ChatOpenAI(
        model=model,
        api_key=_require_api_key(),
        base_url=settings.base_url,
        temperature=temperature,
        request_timeout=settings.llm_timeout_seconds,  # 注意：不是 timeout=
        max_retries=settings.llm_max_retries,
        extra_body=extra_body or None,
    )


def get_chat_model(model: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    """按 (model, temperature) 复用实例，避免每次请求重建 httpx 连接池。"""

    return _build(model or settings.primary_model, temperature)


# ---------------------------------------------------------------- 结构化输出

T = TypeVar("T", bound=BaseModel)


def _unwrap(schema: type[T]) -> Runnable:
    """把 PydanticToolsParser 返回的 list 收敛成单个实例。

    保持与 openai-agents `response.final_output` 一致的返回形状，
    让 workflow 侧的 isinstance 校验和后续代码零改动。
    """

    def _pick(items: list[Any]) -> T:
        if not items:
            raise TypeError(f"模型未返回 {schema.__name__} 结构化结果（tool_calls 为空）")
        obj = items[0]
        if not isinstance(obj, schema):
            raise TypeError(f"期望 {schema.__name__}，实际 {type(obj).__name__}")
        return obj

    return RunnableLambda(_pick)


def structured(llm: ChatOpenAI, schema: type[T]) -> Runnable[Any, T]:
    """思考模式兼容的结构化输出。

    ⚠️ 不能用 `llm.with_structured_output(schema, method="function_calling")`：
    它会把 tool_choice 钉成 `{"type":"function","function":{"name":...}}`，
    而 DeepSeek 思考模式明确拒绝，返回 400 "Thinking mode does not support
    this tool_choice"。openai-agents 之所以能跑，正是因为它没有强制函数名。

    这里改为 tool_choice="auto"，让模型自己决定是否调用该工具，再用官方
    PydanticToolsParser 解析 tool_calls。实测在 thinking=auto / enabled /
    disabled 三种模式下均可用。
    """

    return llm.bind_tools([schema], tool_choice="auto") | PydanticToolsParser(
        tools=[schema]
    ) | _unwrap(schema)
