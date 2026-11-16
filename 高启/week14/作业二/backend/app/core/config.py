"""应用配置及受限的数据目录初始化。"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ConfigurationError(ValueError):
    """配置缺失或不符合项目约束时抛出。"""


@dataclass(frozen=True)
class Settings:
    """集中保存服务地址、模型名和项目数据路径。"""

    environment: str
    data_root: Path
    kafka_bootstrap_servers: str | None
    redis_url: str | None
    mineru_base_url: str | None
    embedding_base_url: str | None
    embedding_model: str
    chat_base_url: str | None
    chat_model: str
    kafka_topic: str = "rag.pdf.ingest"
    kafka_group_id: str = "rag-pdf-workers"
    kafka_partitions: int = 3
    kafka_consumers: int = 2
    chat_max_concurrency: int = 1
    chat_queue_capacity: int = 16
    chat_status_ttl_seconds: int = 604_800
    chat_log_max_bytes: int = 10 * 1024 * 1024
    chat_log_backup_count: int = 5
    max_chat_image_bytes: int = 10 * 1024 * 1024
    max_pdf_bytes: int = 52_428_800
    processing_max_attempts: int = 4
    processing_retry_delays: tuple[int, ...] = (1, 2, 4)
    mineru_task_timeout_seconds: int = 1800
    mineru_poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """从环境变量构造配置；传入映射可隔离测试与本机环境。"""
        values = os.environ if environ is None else environ
        # local/cloud 选择默认根目录；显式 RAG_DATA_ROOT 总是优先。
        environment = values.get("RAG_ENV", "local").strip().lower()
        if environment not in {"local", "cloud"}:
            raise ConfigurationError("RAG_ENV 只能是 local 或 cloud")

        if environment == "local":
            default_root = PROJECT_ROOT / "data"
        else:
            default_root = Path("/workspace/project03")
        root_override = values.get("RAG_DATA_ROOT", "").strip()
        data_root = Path(root_override or default_root).expanduser().resolve()
        return cls(
            environment=environment,
            data_root=data_root,
            kafka_bootstrap_servers=_optional(values, "RAG_KAFKA_BOOTSTRAP_SERVERS"),
            redis_url=_optional(values, "RAG_REDIS_URL"),
            mineru_base_url=_optional(values, "RAG_MINERU_BASE_URL"),
            embedding_base_url=_optional(values, "RAG_EMBEDDING_BASE_URL"),
            embedding_model=values.get("RAG_EMBEDDING_MODEL", "BAAI/bge-m3"),
            chat_base_url=_optional(values, "RAG_CHAT_BASE_URL"),
            chat_model=values.get("RAG_CHAT_MODEL", "Qwen/Qwen3-VL-4B-Instruct"),
            kafka_topic=values.get("RAG_KAFKA_TOPIC", "rag.pdf.ingest"),
            kafka_group_id=values.get("RAG_KAFKA_GROUP_ID", "rag-pdf-workers"),
            kafka_partitions=_positive_int(values, "RAG_KAFKA_PARTITIONS", 3),
            kafka_consumers=_positive_int(values, "RAG_KAFKA_CONSUMERS", 2),
            chat_max_concurrency=_positive_int(values, "RAG_CHAT_MAX_CONCURRENCY", 1),
            chat_queue_capacity=_positive_int(values, "RAG_CHAT_QUEUE_CAPACITY", 16),
            chat_status_ttl_seconds=_positive_int(
                values, "RAG_CHAT_STATUS_TTL_SECONDS", 604_800
            ),
            chat_log_max_bytes=_positive_int(values, "RAG_CHAT_LOG_MAX_BYTES", 10 * 1024 * 1024),
            chat_log_backup_count=_positive_int(values, "RAG_CHAT_LOG_BACKUP_COUNT", 5),
            max_chat_image_bytes=_positive_int(
                values, "RAG_MAX_CHAT_IMAGE_BYTES", 10 * 1024 * 1024
            ),
            max_pdf_bytes=_positive_int(values, "RAG_MAX_PDF_BYTES", 52_428_800),
            processing_max_attempts=_positive_int(values, "RAG_PROCESSING_MAX_ATTEMPTS", 4),
            processing_retry_delays=_retry_delays(
                values, "RAG_PROCESSING_RETRY_DELAYS", "1,2,4"
            ),
            mineru_task_timeout_seconds=_positive_int(
                values, "RAG_MINERU_TASK_TIMEOUT_SECONDS", 1800
            ),
            mineru_poll_interval_seconds=_positive_float(
                values, "RAG_MINERU_POLL_INTERVAL_SECONDS", 2.0
            ),
        )

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """读取项目 .env，并让进程环境变量覆盖文件值。"""
        file_path = env_file or PROJECT_ROOT / ".env"
        values = _read_env_file(file_path) if file_path.is_file() else {}
        # 部署环境可直接注入变量，优先级高于本地 .env。
        values.update(os.environ)
        return cls.from_env(values)

    @property
    def uploads_dir(self) -> Path:
        """返回原始 PDF 上传目录。"""
        return self.data_root / "uploads"

    @property
    def parsed_dir(self) -> Path:
        """返回 MinerU 解析产物目录。"""
        return self.data_root / "parsed"

    @property
    def milvus_dir(self) -> Path:
        """返回 Milvus Lite 数据目录。"""
        return self.data_root / "milvus"

    @property
    def milvus_path(self) -> Path:
        """返回 Milvus Lite 数据库文件路径。"""
        return self.milvus_dir / "milvus_lite.db"

    @property
    def logs_dir(self) -> Path:
        """返回结构化运行日志目录。"""
        return self.data_root / "logs"

    @property
    def models_dir(self) -> Path:
        """返回本地模型缓存目录。"""
        return self.data_root / "models"

    def validate_required_services(self) -> None:
        """在启动真实服务流程前，逐项指出缺失的服务地址。"""
        required = {
            "RAG_KAFKA_BOOTSTRAP_SERVERS": self.kafka_bootstrap_servers,
            "RAG_REDIS_URL": self.redis_url,
            "RAG_MINERU_BASE_URL": self.mineru_base_url,
            "RAG_EMBEDDING_BASE_URL": self.embedding_base_url,
            "RAG_CHAT_BASE_URL": self.chat_base_url,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError("缺少必要服务配置：" + ", ".join(missing))

    def initialize_data_directories(self) -> dict[str, Path]:
        """创建运行目录，并拒绝解析到 data_root 之外的路径。"""
        root = self.data_root.resolve()
        directory_paths = {
            "uploads": self.uploads_dir,
            "parsed": self.parsed_dir,
            "milvus": self.milvus_dir,
            "logs": self.logs_dir,
            "models": self.models_dir,
        }
        initialized_paths: dict[str, Path] = {}
        for name, path in directory_paths.items():
            # resolve 会跟随已有符号链接，避免把写入目录绕出数据根目录。
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ConfigurationError(f"数据目录 {name} 超出 RAG_DATA_ROOT")
            resolved.mkdir(parents=True, exist_ok=True)
            initialized_paths[name] = resolved
        return initialized_paths


def _optional(values: Mapping[str, str], name: str) -> str | None:
    """读取可选配置，并将空白字符串统一视为未设置。"""
    value = values.get(name, "").strip()
    return value or None


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    """读取正整数配置，遇到非法值时返回带变量名的配置错误。"""
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} 必须是正整数") from error
    if value < 1:
        raise ConfigurationError(f"{name} 必须是正整数")
    return value


def _positive_float(values: Mapping[str, str], name: str, default: float) -> float:
    """读取正数型配置，允许轮询间隔使用小数秒。"""
    raw = values.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} 必须是正数") from error
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} 必须是正数")
    return value


def _retry_delays(values: Mapping[str, str], name: str, default: str) -> tuple[int, ...]:
    """读取逗号分隔的正整数退避秒数。"""
    raw = values.get(name, default)
    try:
        delays = tuple(int(value.strip()) for value in raw.split(","))
    except ValueError as error:
        raise ConfigurationError(f"{name} 必须是逗号分隔的正整数") from error
    if not delays or any(delay < 1 for delay in delays):
        raise ConfigurationError(f"{name} 必须包含一个或多个正整数")
    return delays


def _read_env_file(path: Path) -> dict[str, str]:
    """读取简单 KEY=VALUE 文件；不执行 shell 表达式。"""
    values: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ConfigurationError(f"{path}:{line_number} 不是有效的 KEY=VALUE 配置")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values
