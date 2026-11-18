"""环境变量与路径配置。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# sentiment-agent-demo/ 根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

# 优先加载本项目 .env；若不存在可回退到课程目录下 openai-agents-sandbox/.env
_env_path = ROOT_DIR / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv(ROOT_DIR.parent / "openai-agents-sandbox" / ".env")
    load_dotenv()  # 再读进程 cwd，便于临时覆盖


def require_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}，请在 .env 中配置（可参考 .env.example）")
    return value


def get_model_name() -> str:
    return require_env("OPENAI_MODEL", "deepseek-chat")


def get_api_key() -> str:
    return require_env("OPENAI_API_KEY")


def get_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL") or None
