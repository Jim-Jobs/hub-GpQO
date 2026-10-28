"""运行配置。API key 只从环境变量（或本地 .env）读取，不落盘、不进日志。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# .env 是可选便利：装了 python-dotenv 才加载，且不覆盖已有的环境变量。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover - 没装 dotenv 时退回纯环境变量
    pass


THINKING_MODES = ("auto", "enabled", "disabled")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """解析整型环境变量；非法值回退默认，越界值夹到 minimum。"""

    try:
        return max(int(os.getenv(name, default)), minimum)
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
    """解析浮点环境变量；非法值回退默认，越界值夹到 minimum。"""

    try:
        return max(float(os.getenv(name, default)), minimum)
    except ValueError:
        return default


def _env_thinking(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip().lower()
    return value if value in THINKING_MODES else default


@dataclass(frozen=True)
class Settings:
    db_path: Path = PROJECT_ROOT / "ref" / "chinook.db"
    primary_model: str = "deepseek-flash"
    summary_model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    # auto = 不传 thinking 参数，沿用服务端默认（deepseek-flash 默认开启思考模式）
    thinking_mode: str = "auto"
    max_repair_attempts: int = 3
    max_result_rows: int = 200
    max_summary_rows: int = 50
    max_cell_chars: int = 2000
    query_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        # 空串（如 SQL_AGENT_DB=）不能当成相对路径 "."，回退默认库。
        raw_db = (os.getenv("SQL_AGENT_DB") or "").strip()
        db_path = Path(raw_db) if raw_db else Path(str(cls.db_path))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        return cls(
            db_path=db_path,
            primary_model=os.getenv("SQL_AGENT_MODEL", cls.primary_model),
            summary_model=os.getenv("SQL_AGENT_SUMMARY_MODEL", cls.summary_model),
            base_url=os.getenv("OPENAI_BASE_URL", cls.base_url),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
            llm_timeout_seconds=_env_float(
                "SQL_AGENT_LLM_TIMEOUT", cls.llm_timeout_seconds, minimum=1.0
            ),
            llm_max_retries=_env_int("SQL_AGENT_LLM_MAX_RETRIES", cls.llm_max_retries),
            thinking_mode=_env_thinking("SQL_AGENT_THINKING", cls.thinking_mode),
            max_repair_attempts=_env_int(
                "SQL_AGENT_MAX_REPAIR_ATTEMPTS", cls.max_repair_attempts
            ),
            max_result_rows=_env_int("SQL_AGENT_MAX_ROWS", cls.max_result_rows, minimum=1),
            max_summary_rows=_env_int(
                "SQL_AGENT_MAX_SUMMARY_ROWS", cls.max_summary_rows, minimum=1
            ),
            max_cell_chars=_env_int(
                "SQL_AGENT_MAX_CELL_CHARS", cls.max_cell_chars, minimum=1
            ),
            query_timeout_seconds=_env_float(
                "SQL_AGENT_QUERY_TIMEOUT", cls.query_timeout_seconds, minimum=0.1
            ),
        )


settings = Settings.from_env()
