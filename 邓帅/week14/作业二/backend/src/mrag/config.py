"""配置：向上查找 邓帅/.env 并加载，pydantic-settings 校验。"""

from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/mrag/config.py -> ... -> backend/src -> backend -> 作业根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "meta.sqlite"

load_dotenv(find_dotenv(usecwd=True), override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    # LLM（OpenAI 兼容）
    api_key: str
    base_url: str
    base_model: str
    vl_model: str = "qwen3-vl-plus"

    # Zilliz Cloud：数据面用 USER_NAME/PASSWORD，控制面 API 用 TOKEN
    zilliz_uri: str
    zilliz_user_name: str
    zilliz_password: str
    zilliz_token: str = ""
    zilliz_collection: str = "week14_rag_chunks"

    # Kafka
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic: str = "rag-data"
    kafka_group_id: str = "mineru-workers"

    # MinerU：vlm-mlx-engine | pipeline
    mineru_backend: str = "vlm-mlx-engine"
    mineru_timeout_sec: int = 600

    # 检索
    text_top_k: int = 5
    image_top_k: int = 3
    vl_max_images: int = 2

    @property
    def data_dirs(self) -> tuple[Path, Path]:
        return UPLOADS_DIR, PROCESSED_DIR

    def ensure_dirs(self) -> None:
        for d in (UPLOADS_DIR, PROCESSED_DIR, DATA_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """读取配置；缺关键环境变量时抛出可读错误。"""
    try:
        return Settings()
    except Exception as e:  # noqa: BLE001 - 转成可读启动错误
        missing = [
            name
            for name in (
                "api_key",
                "base_url",
                "base_model",
                "zilliz_uri",
                "zilliz_user_name",
                "zilliz_password",
            )
            if not getattr(_raw_probe(), name, "")
        ]
        hint = f"（缺失: {', '.join(missing)}）请检查 邓帅/.env，参考 .env.example" if missing else ""
        raise RuntimeError(f"配置加载失败: {e} {hint}") from e


def _raw_probe() -> dict:
    import os

    keys = {
        "api_key": "API_KEY",
        "base_url": "BASE_URL",
        "base_model": "BASE_MODEL",
        "zilliz_uri": "ZILLIZ_URI",
        "zilliz_user_name": "ZILLIZ_USER_NAME",
        "zilliz_password": "ZILLIZ_PASSWORD",
    }
    return {k: os.getenv(v, "") for k, v in keys.items()}
