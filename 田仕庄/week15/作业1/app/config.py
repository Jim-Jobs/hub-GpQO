from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EngineName = Literal["langgraph", "openai-agents"]


class Settings(BaseSettings):
    """进程配置。优先级：进程环境变量 > .env > 代码默认值。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    pipeline_engine: EngineName = "langgraph"
    dashscope_api_key: str = Field(min_length=1)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-flash"
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/sentiment_chat"
    db_password: str | None = None
    log_level: str = "INFO"
    max_input_length: int = Field(default=4000, gt=0)
    llm_timeout_seconds: float = Field(default=30, gt=0)
    api_token: SecretStr | None = None

    @field_validator("api_token", mode="before")
    @classmethod
    def _normalize_api_token(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            return value
        token = value.strip()
        if not token:
            return None
        if len(token) < 16:
            raise ValueError("API_TOKEN 至少需要 16 个字符")
        return token

    @property
    def psycopg_database_url(self) -> str:
        """LangGraph checkpointer 使用 psycopg 驱动，需要去掉 SQLAlchemy 方言后缀。"""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


if __name__ == "__main__":
    settings = Settings(_env_file=None, dashscope_api_key="demo-key")
    print(f"engine={settings.pipeline_engine}")
    print(f"model={settings.qwen_model}")
    print(f"database_url={settings.database_url}")
