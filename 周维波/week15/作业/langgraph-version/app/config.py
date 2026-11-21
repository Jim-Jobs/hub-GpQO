"""运行配置：全部通过环境变量注入，方便容器化部署。"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """一次进程生命周期内的只读配置。"""

    api_key: str
    base_url: str | None
    model: str
    temperature: float
    request_timeout: float
    mock: bool


def get_settings() -> Settings:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
    # 没有 API Key 时自动降级为内置规则引擎，保证镜像拉起后 /health 与页面都可直接演示
    mock = _env_bool("PIPELINE_MOCK", default=False) or not api_key
    return Settings(
        api_key=api_key,
        base_url=base_url,
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
        request_timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        mock=mock,
    )
