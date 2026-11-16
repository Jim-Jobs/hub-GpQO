"""配置层单测。"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_defaults_match_docs() -> None:
    s = Settings(_env_file=None)
    assert s.kafka_topic_upload == "pdf.upload"
    assert s.kafka_topic_status == "pdf.parse.status"
    assert s.kafka_topic_dlq == "pdf.upload.dlq"
    assert s.es_vector_dim == 1024
    assert s.embedding_dim == 1024
    assert s.chunk_target_tokens == 600
    assert s.chunk_overlap_tokens == 100
    assert s.chunk_min_tokens == 30
    assert s.chunk_max_tokens == 900
    assert s.rrf_rank_constant == 60
    assert s.top_k_final == 8
    assert s.chat_history_turns == 3


def test_image_budget_matches_vllm_limit() -> None:
    """送模图片总预算必须与 vLLM --limit-mm-per-prompt 的 image 数一致。"""
    s = Settings(_env_file=None)
    assert s.max_user_images == 4
    assert s.max_doc_images == 4
    assert s.total_image_budget == 8


def test_mineru_no_cache_defaults_true() -> None:
    """默认有 15 分钟 URL 缓存，必须显式关掉。"""
    assert Settings(_env_file=None).mineru_no_cache is True


def test_data_dirs_are_derived_and_created(tmp_path: Path) -> None:
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.uploads_dir == tmp_path / "uploads"
    assert s.images_dir == tmp_path / "images"
    assert s.work_dir == tmp_path / "work"

    s.ensure_data_dirs()
    assert s.uploads_dir.is_dir()
    assert s.images_dir.is_dir()
    assert s.work_dir.is_dir()
