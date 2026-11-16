"""任务 1 的配置与跨模块数据契约测试。"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.core.config import ConfigurationError, PROJECT_ROOT, Settings
from backend.app.documents.domain.models import ChunkRecord, DocumentRecord, DocumentStatus, IngestEvent, VectorRecord
from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import ChatRequest, ChatResponse, ChatStatusRecord


def test_settings_isolate_local_and_cloud_data_roots(tmp_path: Path) -> None:
    """验证本机和云端配置可使用独立数据根目录及计划默认值。"""
    local = Settings.from_env({"RAG_ENV": "local", "RAG_DATA_ROOT": str(tmp_path / "local")})
    cloud = Settings.from_env({"RAG_ENV": "cloud", "RAG_DATA_ROOT": str(tmp_path / "cloud")})

    assert local.data_root != cloud.data_root
    assert local.kafka_partitions == 3
    assert local.kafka_consumers == 2
    assert local.chat_max_concurrency == 1
    assert local.chat_queue_capacity == 16
    assert local.chat_status_ttl_seconds == 604800
    assert local.chat_log_max_bytes == 10 * 1024 * 1024
    assert local.chat_log_backup_count == 5
    assert local.max_chat_image_bytes == 10 * 1024 * 1024
    assert local.milvus_path.is_relative_to(local.data_root)
    assert Settings.from_env({"RAG_DATA_ROOT": ""}).data_root == (PROJECT_ROOT / "data").resolve()


def test_dotenv_values_are_overridden_by_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证进程环境变量优先于 .env 文件中的同名值。"""
    env_file = tmp_path / ".env"
    env_file.write_text("RAG_ENV=local\nRAG_KAFKA_TOPIC=from-file\n", encoding="utf-8")
    monkeypatch.setenv("RAG_KAFKA_TOPIC", "from-process")

    settings = Settings.load(env_file)

    assert settings.kafka_topic == "from-process"


def test_missing_required_service_address_has_actionable_error() -> None:
    """验证关键服务地址缺失时错误信息能指出配置项名称。"""
    settings = Settings.from_env({})

    with pytest.raises(ConfigurationError, match="RAG_KAFKA_BOOTSTRAP_SERVERS"):
        settings.validate_required_services()


def test_invalid_numeric_configuration_is_rejected() -> None:
    """验证分区数等正整数配置拒绝零值。"""
    with pytest.raises(ConfigurationError, match="RAG_KAFKA_PARTITIONS"):
        Settings.from_env({"RAG_KAFKA_PARTITIONS": "0"})
    with pytest.raises(ConfigurationError, match="RAG_CHAT_QUEUE_CAPACITY"):
        Settings.from_env({"RAG_CHAT_QUEUE_CAPACITY": "0"})


def test_processing_retry_policy_defaults_to_four_attempts_and_one_two_four_seconds() -> None:
    """验证文档处理的默认总尝试次数和退避秒数符合计划。"""
    settings = Settings.from_env({})

    assert settings.processing_max_attempts == 4
    assert settings.processing_retry_delays == (1, 2, 4)


def test_mineru_task_wait_defaults_and_overrides_are_explicit() -> None:
    """MinerU 轮询默认值和环境覆盖项可被明确读取。"""
    defaults = Settings.from_env({})
    configured = Settings.from_env(
        {
            "RAG_MINERU_TASK_TIMEOUT_SECONDS": "7200",
            "RAG_MINERU_POLL_INTERVAL_SECONDS": "1.5",
        }
    )

    assert defaults.mineru_task_timeout_seconds == 1800
    assert defaults.mineru_poll_interval_seconds == 2
    assert configured.mineru_task_timeout_seconds == 7200
    assert configured.mineru_poll_interval_seconds == 1.5


def test_chat_queue_status_and_log_configuration_can_be_overridden() -> None:
    """问答队列、状态 TTL、图片大小和日志轮转支持环境变量覆盖。"""
    settings = Settings.from_env(
        {
            "RAG_CHAT_MAX_CONCURRENCY": "2",
            "RAG_CHAT_QUEUE_CAPACITY": "32",
            "RAG_CHAT_STATUS_TTL_SECONDS": "3600",
            "RAG_CHAT_LOG_MAX_BYTES": "4096",
            "RAG_CHAT_LOG_BACKUP_COUNT": "3",
            "RAG_MAX_CHAT_IMAGE_BYTES": "2048",
        }
    )

    assert settings.chat_max_concurrency == 2
    assert settings.chat_queue_capacity == 32
    assert settings.chat_status_ttl_seconds == 3600
    assert settings.chat_log_max_bytes == 4096
    assert settings.chat_log_backup_count == 3
    assert settings.max_chat_image_bytes == 2048


def test_invalid_mineru_poll_interval_is_rejected() -> None:
    """MinerU 轮询间隔必须是有限正数。"""
    for invalid_value in ("0", "-1", "nan", "inf"):
        with pytest.raises(ConfigurationError, match="RAG_MINERU_POLL_INTERVAL_SECONDS"):
            Settings.from_env({"RAG_MINERU_POLL_INTERVAL_SECONDS": invalid_value})


def test_invalid_processing_retry_delay_is_rejected() -> None:
    """验证文档重试退避配置拒绝非正整数。"""
    with pytest.raises(ConfigurationError, match="RAG_PROCESSING_RETRY_DELAYS"):
        Settings.from_env({"RAG_PROCESSING_RETRY_DELAYS": "1,0,2"})


def test_data_directories_are_created_only_under_data_root(tmp_path: Path) -> None:
    """验证运行目录创建在配置的数据根目录内。"""
    settings = Settings.from_env({"RAG_DATA_ROOT": str(tmp_path / "data")})

    directories = settings.initialize_data_directories()

    assert set(directories) == {"uploads", "parsed", "milvus", "logs", "models"}
    assert all(path.is_dir() and path.is_relative_to(settings.data_root) for path in directories.values())


def test_data_directory_symlink_cannot_escape_root(tmp_path: Path) -> None:
    """验证目录符号链接不能将运行数据引导到根目录外。"""
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "uploads").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不允许创建符号链接：{error}")
    settings = Settings.from_env({"RAG_DATA_ROOT": str(root)})

    with pytest.raises(ConfigurationError, match="超出 RAG_DATA_ROOT"):
        settings.initialize_data_directories()


def test_document_and_ingest_contracts_round_trip_json() -> None:
    """验证 Kafka 任务及 Redis 文档记录可往返 JSON 序列化。"""
    created_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
    event = IngestEvent("event-1", "doc-1", "sample.pdf", "uploads/doc-1.pdf", created_at, 0)
    document = DocumentRecord("doc-1", "sample.pdf", DocumentStatus.PENDING)

    assert IngestEvent.from_json(event.to_json()) == event
    assert DocumentRecord.from_json(document.to_json()) == document


def test_vector_contract_round_trips_chunk_metadata_and_embedding() -> None:
    """验证向量记录序列化后保留文本块来源和向量值。"""
    vector = VectorRecord(ChunkRecord("chunk-1", "doc-1", 4, 0, "text", ["asset-1"]), [0.1, 0.2])

    assert VectorRecord.from_json(vector.to_json()) == vector


def test_chat_contracts_round_trip_json() -> None:
    """验证问答状态及嵌套响应可往返 JSON 序列化。"""
    request = ChatRequest("question", b"image bytes")
    response = ChatResponse("in_scope", [{"type": "text", "text": "answer"}], [], [])
    record = ChatStatusRecord("chat-1", ChatStatus.ACCEPTED, ChatStage.SCOPE_AND_REWRITE, response=response)

    assert request.image == b"image bytes"
    assert ChatStatusRecord.from_json(record.to_json()) == record
    assert ChatResponse.from_json(response.to_json()) == response
