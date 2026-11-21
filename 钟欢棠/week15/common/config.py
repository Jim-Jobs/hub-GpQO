"""项目共享配置：统一读取 .env，并支持无密钥的 mock 模式。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 从当前工作目录或项目根目录加载 .env
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv()


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def settings() -> "Settings":
    return Settings()


class Settings:
    """运行配置。未配置 OPENAI_API_KEY 时自动进入 mock 模式。"""

    def __init__(self) -> None:
        self.api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
        self.base_url: str = os.getenv("OPENAI_BASE_URL", "").strip()
        self.model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        self.force_mock: bool = _flag("USE_MOCK_LLM")
        self.mock: bool = self.force_mock or not self.api_key

    def describe(self) -> str:
        mode = "mock（无需 API Key）" if self.mock else f"真实模型 {self.model}"
        return f"运行模式：{mode}"


def require_real_model() -> None:
    """真实模式下校验密钥，避免运行时抛出难以理解的错误。"""
    cfg = settings()
    if cfg.mock:
        raise RuntimeError(
            "当前为 mock 模式。请设置 OPENAI_API_KEY 后重启服务以调用真实模型。"
        )
