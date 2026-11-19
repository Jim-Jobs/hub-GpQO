"""全局配置。

所有后端服务都从项目根目录的 ``.env`` 读取配置，因此必须以项目根目录为工作目录
启动服务（``scripts/run_service.py`` 会自动处理）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: 项目根目录：backends/shared/config/settings.py -> backends/shared/config -> backends/shared -> 根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """``.env`` 中可配置项的强类型映射。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- OpenAI / 模型 ---
    openai_api_key: str = ""
    #: 存放 API Key 的文件路径（Docker secret 场景）。为空时只看 openai_api_key。
    openai_api_key_file: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None
    temperature: float = 0.2
    max_output_tokens: int = 1000

    # --- 前端使用的 API 地址（后端仅作记录，Vite 直接读取 VITE_* 变量） ---
    frontend_langgraph_api_url: str = "http://localhost:8000"
    frontend_agents_sdk_api_url: str = "http://localhost:9000"

    # --- 各 Agent 服务的地址 ---
    langgraph_agent1_url: str = "http://127.0.0.1:8001"
    langgraph_agent2_url: str = "http://127.0.0.1:8002"
    agents_sdk_agent1_url: str = "http://127.0.0.1:9001"
    agents_sdk_agent2_url: str = "http://127.0.0.1:9002"

    # --- 数据库（两套框架各自独立，禁止共享） ---
    langgraph_database_path: str = "./data/langgraph/conversations.db"
    agents_sdk_database_path: str = "./data/openai_agents/conversations.db"

    # --- Agent HTTP 调用超时（秒） ---
    agent_request_timeout: float = 90.0


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试中可用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
