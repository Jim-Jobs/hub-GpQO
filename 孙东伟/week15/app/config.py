"""运行配置：全部来自环境变量，便于本地 / Docker / 云上一致。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    default_engine: str
    timeout: float
    force_mock: bool

    @property
    def mode(self) -> str:
        """没有 Key，或显式要求 mock 时，走离线规则引擎。"""
        return "mock" if (self.force_mock or not self.api_key) else "live"


def load_settings() -> Settings:
    mode = _env("APP_MODE", default="auto").lower()
    return Settings(
        api_key=_env("LLM_API_KEY", "OPENAI_API_KEY"),
        base_url=_env("LLM_BASE_URL", default="https://api.openai.com/v1"),
        model=_env("LLM_MODEL", default="gpt-4o-mini"),
        default_engine=_env("DEFAULT_ENGINE", "ENGINE", default="langgraph").lower(),
        timeout=float(_env("LLM_TIMEOUT", default="60")),
        force_mock=(mode == "mock"),
    )


settings = load_settings()
