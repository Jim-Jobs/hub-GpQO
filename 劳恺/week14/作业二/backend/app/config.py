"""配置层 —— 所有可调项的唯一来源。

对应文档：``docs/07-部署指南.md`` §6「环境变量」、``docs/09-开发计划.md`` §7「开发规范」。
约束：**禁止**在业务代码里硬编码路径 / URL / 密钥，一律走 ``Settings``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。环境变量名与字段名大小写不敏感（``MOCK_MODE`` → ``mock_mode``）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 通用 ----------
    mock_mode: bool = False
    """总开关：为真时 MinerU / LLM / Embedding 全部走桩实现，不产生任何 GPU 与外网费用。"""
    mock_mineru: bool | None = None
    """单独覆盖 MinerU：``None`` 跟随 ``mock_mode``。

    用于「真 MinerU + 假 LLM」这类混合调试（例如没有 GPU 但想验证解析入库）。
    """
    mock_llm: bool | None = None
    """单独覆盖 LLM：``None`` 跟随 ``mock_mode``。"""
    data_dir: Path = Path("/data")
    log_level: str = "INFO"

    # ---------- API ----------
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    max_upload_mb: int = 100
    max_files_per_upload: int = 20

    # ---------- Kafka ----------
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_topic_upload: str = "pdf.upload"
    kafka_topic_status: str = "pdf.parse.status"
    kafka_topic_dlq: str = "pdf.upload.dlq"
    kafka_consumer_group: str = "pdf-parser"
    kafka_status_consumer_group: str = "status-bus"
    kafka_partitions: int = 2
    kafka_max_attempts: int = 3
    """解析失败重试上限，超过则进 ``pdf.upload.dlq`` 并把文档置为 failed。"""
    kafka_api_version: str = "3.9.0"
    """显式指定 broker 版本（``major.minor.patch``）。

    必须显式给：kafka-python 的自动版本探测在这个 KRaft 单节点上会超时，
    导致 ``KafkaTimeoutError: Unable to bootstrap``。
    """

    # ---------- Elasticsearch ----------
    es_url: str = "http://elasticsearch:9200"
    es_index_documents: str = "mmrag-documents"
    es_index_chunks: str = "mmrag-chunks"
    es_index_images: str = "mmrag-images"
    es_index_sessions: str = "mmrag-sessions"
    es_vector_dim: int = 1024
    es_request_timeout: float = 30.0

    # ---------- vLLM ----------
    llm_base_url: str = "http://vllm:8000/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "qwen3-vl"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.2
    llm_image_max_pixels: int = 1003520

    # ---------- Embedding ----------
    embedding_backend: str = "openai"
    """``openai`` | ``local`` | ``mock``。``openai`` 下图片路降级为 caption 文本向量。"""
    embedding_base_url: str = "http://embed:8000/v1"
    embedding_api_key: str = "EMPTY"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_local_model_path: str = "/models/Qwen3-VL-Embedding-0.6B"

    # ---------- MinerU ----------
    mineru_base_url: str = "https://mineru.net/api"
    mineru_api_key: str = ""
    mineru_submit_mode: str = "upload"
    """``upload``（两段式签名 PUT，默认）| ``url``。"""
    mineru_task_path: str = "/v4/extract/task"
    mineru_result_path: str = "/v4/extract/task/{task_id}"
    mineru_file_urls_path: str = "/v4/file-urls/batch"
    mineru_batch_result_path: str = "/v4/extract-results/batch/{batch_id}"
    mineru_model_version: str = "vlm"
    mineru_is_ocr: bool = False
    mineru_enable_table: bool = True
    mineru_enable_formula: bool = True
    mineru_language: str = "ch"
    mineru_no_cache: bool = True
    """必须为真：默认有 15 分钟 URL 缓存，否则重解析会拿到旧结果。"""
    mineru_page_ranges: str = ""
    """留空=全量；如 ``"1-50"`` 可省配额。"""
    mineru_poll_interval: float = 3.0
    mineru_poll_timeout: float = 1800.0
    parse_timeout_seconds: float = 1800.0
    """单文档解析超时兜底，超时判 failed（绝不允许永远卡在 processing）。"""

    # ---------- 检索 ----------
    top_k_bm25: int = 20
    top_k_vector: int = 20
    top_k_final: int = 8
    rrf_rank_constant: int = 60
    max_doc_images: int = 4
    max_user_images: int = 4
    image_max_edge: int = 1600
    image_jpeg_quality: int = 85

    # ---------- 切块 ----------
    chunk_target_tokens: int = 600
    chunk_overlap_tokens: int = 100
    chunk_min_tokens: int = 30
    chunk_max_tokens: int = 900

    # ---------- 会话 ----------
    chat_history_turns: int = 3

    # ---------- 派生路径 ----------
    @property
    def uploads_dir(self) -> Path:
        """原始上传的 PDF。"""
        return self.data_dir / "uploads"

    @property
    def images_dir(self) -> Path:
        """从 PDF 抽取的图片（统一转 PNG）。"""
        return self.data_dir / "images"

    @property
    def work_dir(self) -> Path:
        """MinerU 产物的解压临时目录，解析成功后即可删除。"""
        return self.data_dir / "work"

    def ensure_data_dirs(self) -> None:
        """创建数据目录（幂等）。"""
        for path in (self.data_dir, self.uploads_dir, self.images_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def total_image_budget(self) -> int:
        """送模图片总上限，需与 vLLM ``--limit-mm-per-prompt`` 的 image 数对齐。"""
        return self.max_user_images + self.max_doc_images

    @property
    def use_mock_mineru(self) -> bool:
        return self.mock_mode if self.mock_mineru is None else self.mock_mineru

    @property
    def use_mock_llm(self) -> bool:
        return self.mock_mode if self.mock_llm is None else self.mock_llm


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例。测试里用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()


settings = get_settings()
