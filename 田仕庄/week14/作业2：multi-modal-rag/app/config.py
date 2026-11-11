"""唯一配置入口：所有环境变量都在这里读取与派生。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """轻量 .env 加载：不覆盖已有环境变量，支持引号与注释。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


class Settings:
    """运行配置。测试通过环境变量覆盖后重建（清空 get_settings 缓存）。"""

    log_format = "%(asctime)s %(levelname)s %(process)d %(name)s [%(trace_id)s] %(message)s"

    def __init__(self) -> None:
        self.mock_external = _env_bool("RAG_MOCK_EXTERNAL", False)

        # 数据根：所有运行时数据都在这一棵树下
        self.data_dir = _env_path("RAG_DATA_DIR", REPO_ROOT / "data")
        self.uploads_dir = _env_path("RAG_UPLOAD_DIR", self.data_dir / "uploads")
        self.processed_dir = _env_path("RAG_PROCESSED_DIR", self.data_dir / "processed")
        self.questions_dir = _env_path("RAG_QUESTIONS_DIR", self.data_dir / "questions")
        self.db_path = _env_path("RAG_DB_PATH", self.data_dir / "db" / "app.db")
        self.logs_dir = _env_path("RAG_LOG_DIR", self.data_dir / "logs")
        self.frontend_dir = _env_path("RAG_FRONTEND_DIR", REPO_ROOT / "frontend")

        # 日志
        self.log_level = os.getenv("RAG_LOG_LEVEL", "INFO").upper()
        self.log_json = _env_bool("RAG_LOG_JSON", False)
        self.log_max_bytes = _env_int("RAG_LOG_MAX_BYTES", 10 * 1024 * 1024)
        self.log_backup_count = _env_int("RAG_LOG_BACKUP_COUNT", 5)

        # Kafka
        self.kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
        self.kafka_topic = os.getenv("KAFKA_TOPIC", "rag-data")
        self.kafka_group_id = os.getenv("RAG_KAFKA_GROUP_ID", "rag-parse-workers")
        self.kafka_max_poll_interval_ms = _env_int(
            "RAG_KAFKA_MAX_POLL_INTERVAL_MS", 1_800_000
        )

        # 解析
        default_backend = "mock" if self.mock_external else "mineru"
        self.parse_backend = os.getenv("RAG_PARSE_BACKEND", default_backend)
        self.mineru_backend = os.getenv("RAG_MINERU_BACKEND", "vlm-http-client")
        self.mineru_backend_url = os.getenv(
            "MINERU_BACKEND_URL", "http://127.0.0.1:30000"
        )
        self.mineru_cli_path = os.getenv("RAG_MINERU_CLI_PATH", "")
        self.mineru_cli_mode = os.getenv("RAG_MINERU_CLI_MODE", "v4")
        self.mineru_tier = os.getenv("RAG_MINERU_TIER", "flash")
        self.mineru_server_url = os.getenv(
            "RAG_MINERU_SERVER_URL", "http://127.0.0.1:15980"
        )
        self.parse_timeout_sec = _env_int("RAG_PARSE_TIMEOUT_SEC", 900)
        self.parse_retries = _env_int("RAG_PARSE_RETRIES", 2)
        self.worker_retry_base_sec = _env_float("RAG_WORKER_RETRY_BASE_SEC", 5.0)
        self.worker_poll_interval_sec = _env_float("RAG_WORKER_POLL_INTERVAL_SEC", 1.0)
        self.worker_dlq_path = _env_path(
            "RAG_WORKER_DLQ_PATH", self.logs_dir / "worker_dlq.jsonl"
        )

        # 向量库
        self.milvus_uri = os.getenv("RAG_MILVUS_URI") or os.getenv(
            "MILVUS_URI", ""
        )
        self.milvus_token = os.getenv("MILVUS_TOKEN", "")
        self.milvus_collection = os.getenv("MILVUS_COLLECTION", "rag_data_new")

        # 向量模型
        self.bge_model_path = os.getenv("BGE_MODEL_PATH", "")
        self.clip_model_path = os.getenv("CLIP_MODEL_PATH", "")
        self.bge_dim = _env_int("BGE_DIM", 512)
        self.clip_dim = _env_int("CLIP_DIM", 512)
        self.embed_batch_size = _env_int("RAG_EMBED_BATCH_SIZE", 16)

        # 生成模型
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.dashscope_base_url = os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.llm_model = os.getenv("LLM_MODEL", "qwen3.8-flash")
        self.vl_model = os.getenv("VL_MODEL", "qwen3-vl-plus")

        # 上传与问答图片
        self.max_upload_files = _env_int("RAG_MAX_UPLOAD_FILES", 20)
        self.max_upload_bytes = _env_int("RAG_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
        self.max_question_images = _env_int("RAG_MAX_QUESTION_IMAGES", 4)
        self.max_question_image_bytes = _env_int(
            "RAG_MAX_QUESTION_IMAGE_BYTES", 5 * 1024 * 1024
        )
        self.question_image_ttl_hours = _env_int(
            "RAG_QUESTION_IMAGE_TTL_HOURS", 24
        )
        self.search_top_k = _env_int("RAG_SEARCH_TOP_K", 5)
        self.max_vision_images = _env_int("RAG_MAX_VISION_IMAGES", 4)
        self.user_image_retrieval = os.getenv("RAG_USER_IMAGE_RETRIEVAL", "none")

        # 补偿任务
        self.sweep_enabled = _env_bool("RAG_SWEEP_ENABLED", True)
        self.sweep_interval_sec = _env_int("RAG_SWEEP_INTERVAL_SEC", 60)
        self.pending_stale_sec = _env_int("RAG_PENDING_STALE_SEC", 600)
        self.max_requeue = _env_int("RAG_MAX_REQUEUE", 2)

        self.api_public_base_url = os.getenv(
            "API_PUBLIC_BASE_URL", "http://127.0.0.1:8000"
        ).rstrip("/")

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.processed_dir,
            self.questions_dir,
            self.db_path.parent,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
