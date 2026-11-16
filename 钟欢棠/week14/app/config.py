"""全局配置：优先读取环境变量，其次读取项目根目录的 .env。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv 是可选依赖
    pass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# 文档解析状态（功能 2）
STATUS_PENDING = "待解析"
STATUS_PARSING = "解析中"
STATUS_DONE = "解析完成"
STATUS_FAILED = "解析失败"


@dataclass(frozen=True)
class Settings:
    # kafka = 真实 Redis + Kafka（作业提交用）；local = 无 Docker 本机预览
    app_mode: str = os.getenv("APP_MODE", "kafka").strip().lower()
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    kafka_bootstrap: str = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    kafka_topic_prefix: str = os.getenv("KAFKA_TOPIC_PREFIX", "mmrag")
    data_dir: Path = Path(os.getenv("DATA_DIR") or str(ROOT / "data")).resolve()
    media_prefix: str = "/media"

    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _int("PORT", 8000)

    # PDF 解析
    parser_backend: str = os.getenv("PARSER_BACKEND", "auto")
    mineru_bin: str = os.getenv("MINERU_BIN", "mineru")
    mineru_extra_args: str = os.getenv("MINERU_EXTRA_ARGS", "")
    mineru_api_url: str = os.getenv("MINERU_API_URL", "")
    mineru_timeout: int = _int("MINERU_TIMEOUT", 900)

    # 大模型（OpenAI 兼容协议）
    llm_api_key: str = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm_model: str = os.getenv("LLM_MODEL", "qwen-vl-max-latest")
    llm_text_model: str = os.getenv("LLM_TEXT_MODEL", "qwen-max")
    embed_model: str = os.getenv("EMBED_MODEL", "text-embedding-v3")
    embed_enabled: bool = _bool("EMBED_ENABLED", True)
    vision_enabled: bool = _bool("LLM_VISION_ENABLED", True)
    top_k: int = _int("TOP_K", 5)
    max_context_images: int = _int("MAX_CONTEXT_IMAGES", 3)

    session_ttl: int = _int("SESSION_TTL", 7 * 24 * 3600)
    qa_ttl: int = _int("QA_TTL", 3600)
    chunk_size: int = _int("CHUNK_SIZE", 700)
    chunk_overlap: int = _int("CHUNK_OVERLAP", 80)

    @property
    def topics(self) -> dict[str, str]:
        p = self.kafka_topic_prefix
        return {
            "parse_tasks": f"{p}.pdf.tasks",       # 网页 -> 后端解析 Worker
            "parse_status": f"{p}.pdf.status",     # Worker -> 网页（解析状态事件）
            "qa_requests": f"{p}.qa.requests",     # 网页 -> 后端问答 Worker
            "qa_responses": f"{p}.qa.responses",   # Worker -> 网页（回答结果）
            "events": f"{p}.events",               # 全链路事件流，前端可视化
        }

    @property
    def local_mode(self) -> bool:
        """无 Docker 预览模式：单进程消息总线 + 文件版状态存储（Topic/流程与 Kafka 版一致）。"""
        return self.app_mode in {"local", "standalone", "demo", "no-docker"}

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    def media_url(self, relative_path: str) -> str:
        return f"{self.media_prefix}/{relative_path.lstrip('/')}"

    def media_path(self, url: str) -> Path:
        """把 /media/... URL 还原成本地文件路径，并阻止目录穿越。"""
        prefix = self.media_prefix + "/"
        relative = url[len(prefix):] if url.startswith(prefix) else url.lstrip("/")
        root = self.data_dir.resolve()
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"非法的媒体路径：{url}")
        return target


settings = Settings()
