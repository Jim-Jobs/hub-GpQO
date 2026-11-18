import asyncio

import httpx
import openai
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from app.config import Settings
from app.domain.errors import (
    AppError,
    llm_rate_limited_error,
    llm_timeout_error,
    llm_unavailable_error,
)


def build_async_openai_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.qwen_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
    )


def build_chat_openai(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.qwen_model,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
        temperature=0,
    )


def _iter_cause_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def translate_llm_error(exc: BaseException) -> AppError | None:
    """把底层 SDK / 网络异常映射为契约错误；未知异常返回 None 交由 500 处理。"""
    for item in _iter_cause_chain(exc):
        name = type(item).__name__
        if isinstance(item, (asyncio.TimeoutError, httpx.TimeoutException)):
            return llm_timeout_error()
        if isinstance(item, openai.APITimeoutError) or "Timeout" in name:
            return llm_timeout_error()
        if isinstance(item, openai.RateLimitError) or _status_code(item) == 429:
            return llm_rate_limited_error()
        if isinstance(
            item,
            (openai.APIConnectionError, openai.APIStatusError, httpx.TransportError),
        ):
            return llm_unavailable_error()
    return None


if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        dashscope_api_key="demo-key",
        qwen_base_url="https://example.com/v1",
    )
    client = build_async_openai_client(settings)
    print("base_url:", client.base_url)
    print("timeout:", settings.llm_timeout_seconds)
