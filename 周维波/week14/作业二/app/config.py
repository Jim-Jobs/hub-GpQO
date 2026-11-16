"""全局配置：全部通过环境变量覆盖，未配置时使用本机可跑的默认值。

设计原则：
1. 不依赖任何配置框架（pydantic-settings 之外的多余依赖），只读 os.environ；
2. 所有路径都是绝对路径，便于 API 与 Worker 两个进程共享；
3. 每个能力（解析 / 向量化 / 向量库 / LLM）都有可切换的后端，
   开发环境用轻量实现，生产环境切到 MinerU + bge/CLIP + Milvus + VLM。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _str(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value.strip() == "" else value.strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def _path(key: str, default: Path) -> Path:
    raw = os.getenv(key)
    candidate = Path(raw).expanduser() if raw else default
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class Settings:
    # ---------- 通用 ----------
    project_root: Path
    data_dir: Path
    upload_dir: Path
    parsed_dir: Path
    tmp_dir: Path
    database_url: str

    # ---------- API ----------
    api_host: str
    api_port: int
    public_base_url: str

    # ---------- Kafka ----------
    kafka_bootstrap: str
    topic_parse_request: str
    topic_parse_status: str
    topic_parse_dlq: str
    worker_group: str
    kafka_partitions: int
    kafka_replication: int

    # ---------- 上传 ----------
    max_upload_mb: int
    max_files_per_upload: int

    # ---------- 解析 ----------
    parser_backend: str
    mineru_executable: str
    mineru_backend: str
    mineru_api_url: str
    mineru_timeout_sec: int
    mineru_extra_args: str
    chunk_size: int

    # ---------- 向量化 ----------
    embed_backend: str
    text_embed_model: str
    clip_model: str
    text_dim: int
    clip_dim: int
    embed_device: str
    embed_batch_size: int

    # ---------- 向量库 ----------
    vector_store: str
    milvus_uri: str
    milvus_token: str
    milvus_collection: str

    # ---------- 检索 / 问答 ----------
    top_k_text: int
    top_k_image: int
    max_context_chars: int
    send_retrieved_images: bool
    max_images_to_llm: int
    llm_backend: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout_sec: int

    # ---------- Worker ----------
    parse_max_attempts: int
    retry_backoff_sec: float


def _build_settings() -> Settings:
    data_dir = _path("DATA_DIR", PROJECT_ROOT / "data")
    upload_dir = data_dir / "uploads"
    parsed_dir = data_dir / "parsed"
    tmp_dir = data_dir / "tmp"
    for directory in (upload_dir, parsed_dir, tmp_dir):
        directory.mkdir(parents=True, exist_ok=True)

    api_port = _int("API_PORT", 8000)
    public_base_url = _str("PUBLIC_BASE_URL", f"http://127.0.0.1:{api_port}").rstrip("/")

    db_path = _path("SQLITE_PATH", data_dir / "app.db")
    database_url = _str("DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")

    vector_store = _str(
        "VECTOR_STORE", "milvus" if os.getenv("MILVUS_URI") else "sqlite"
    ).lower()

    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        upload_dir=upload_dir,
        parsed_dir=parsed_dir,
        tmp_dir=tmp_dir,
        database_url=database_url,
        api_host=_str("API_HOST", "127.0.0.1"),
        api_port=api_port,
        public_base_url=public_base_url,
        kafka_bootstrap=_str("KAFKA_BOOTSTRAP", "127.0.0.1:9092"),
        topic_parse_request=_str("TOPIC_PARSE_REQUEST", "rag.parse.request"),
        topic_parse_status=_str("TOPIC_PARSE_STATUS", "rag.parse.status"),
        topic_parse_dlq=_str("TOPIC_PARSE_DLQ", "rag.parse.dlq"),
        worker_group=_str("WORKER_GROUP", "rag-parse-workers"),
        kafka_partitions=_int("KAFKA_PARTITIONS", 3),
        kafka_replication=_int("KAFKA_REPLICATION", 1),
        max_upload_mb=_int("MAX_UPLOAD_MB", 100),
        max_files_per_upload=_int("MAX_FILES_PER_UPLOAD", 20),
        parser_backend=_str("PARSER_BACKEND", "mineru").lower(),
        mineru_executable=_str("MINERU_EXECUTABLE", "mineru"),
        mineru_backend=_str("MINERU_BACKEND", "pipeline"),
        mineru_api_url=_str("MINERU_API_URL", "http://127.0.0.1:30000"),
        mineru_timeout_sec=_int("MINERU_TIMEOUT_SEC", 900),
        mineru_extra_args=_str("MINERU_EXTRA_ARGS", ""),
        chunk_size=_int("CHUNK_SIZE", 800),
        embed_backend=_str("EMBED_BACKEND", "local").lower(),
        text_embed_model=_str(
            "TEXT_EMBED_MODEL", "BAAI/bge-small-zh-v1.5"
        ),
        clip_model=_str("CLIP_MODEL", "jinaai/jina-clip-v2"),
        text_dim=_int("TEXT_DIM", 512),
        clip_dim=_int("CLIP_DIM", 1024),
        embed_device=_str("EMBED_DEVICE", "cpu"),
        embed_batch_size=_int("EMBED_BATCH_SIZE", 8),
        vector_store=vector_store,
        milvus_uri=_str("MILVUS_URI", ""),
        milvus_token=_str("MILVUS_TOKEN", ""),
        milvus_collection=_str("MILVUS_COLLECTION", "rag_chunks"),
        top_k_text=_int("TOP_K_TEXT", 5),
        top_k_image=_int("TOP_K_IMAGE", 3),
        max_context_chars=_int("MAX_CONTEXT_CHARS", 6000),
        send_retrieved_images=_bool("SEND_RETRIEVED_IMAGES", True),
        max_images_to_llm=_int("MAX_IMAGES_TO_LLM", 3),
        llm_backend=_str("LLM_BACKEND", "openai").lower(),
        llm_base_url=_str(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/"),
        llm_api_key=_str("LLM_API_KEY", ""),
        llm_model=_str("LLM_MODEL", "qwen-vl-max"),
        llm_temperature=_float("LLM_TEMPERATURE", 0.2),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 1500),
        llm_timeout_sec=_int("LLM_TIMEOUT_SEC", 120),
        parse_max_attempts=_int("PARSE_MAX_ATTEMPTS", 3),
        retry_backoff_sec=_float("RETRY_BACKOFF_SEC", 3.0),
    )


settings = _build_settings()
