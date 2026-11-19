"""模型配置。

本模块只提供**框架无关**的 LLM 连接参数；具体的模型对象（LangChain 的
``ChatOpenAI``、Agents SDK 的 ``OpenAIChatCompletionsModel``）由各自的后端包构造，
避免 ``backends.shared`` 反向依赖某个 Agent 框架。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backends.shared.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMConfig:
    """一个可用的模型连接配置。"""

    model: str
    api_key: str
    base_url: str | None
    temperature: float
    max_output_tokens: int


def _read_api_key() -> str:
    """读取 API Key。

    优先级：

    1. ``OPENAI_API_KEY`` 环境变量（或项目根目录 ``.env``）
    2. ``OPENAI_API_KEY_FILE`` 指向的文件（Docker secret 常用这种形式，
       key 不会出现在环境变量里，也不会留在镜像的环境层中）
    """
    settings = get_settings()

    api_key = (settings.openai_api_key or "").strip()
    if api_key:
        return api_key

    key_file = (settings.openai_api_key_file or "").strip()
    if key_file:
        path = Path(key_file)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content

    raise RuntimeError(
        "未配置 API Key。请设置环境变量 OPENAI_API_KEY，"
        "或 OPENAI_API_KEY_FILE 指向一个存放 key 的文件"
        "（可参考 .env.example）。"
    )


def get_llm_config() -> LLMConfig:
    """读取模型配置。未配置 API Key 时直接报错，而不是等到调用时才失败。"""
    settings = get_settings()
    api_key = _read_api_key()
    base_url = (settings.openai_base_url or "").strip() or None
    return LLMConfig(
        model=settings.openai_model,
        api_key=api_key,
        base_url=base_url,
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
    )


def has_api_key() -> bool:
    """健康检查用：只判断配置是否存在，不触发异常。"""
    try:
        _read_api_key()
    except RuntimeError:
        return False
    return True
