"""config 单测：真实 .env 加载 + 缺失字段报错。"""

import pytest

from mrag.config import DATA_DIR, PROCESSED_DIR, UPLOADS_DIR, Settings, get_settings


def test_settings_loads_from_env_file():
    s = get_settings()
    assert s.api_key
    assert s.base_url
    assert s.zilliz_uri.startswith("https://")
    assert s.zilliz_user_name
    assert s.zilliz_password
    assert s.vl_model == "qwen3-vl-plus"
    assert s.kafka_topic == "rag-data"
    assert s.mineru_backend in ("vlm-mlx-engine", "pipeline")


def test_defaults():
    s = get_settings()
    assert s.text_top_k == 5
    assert s.image_top_k == 3
    assert s.vl_max_images == 2
    assert s.mineru_timeout_sec == 600


def test_missing_required_raises(monkeypatch):
    for key in ("API_KEY", "BASE_URL", "BASE_MODEL", "ZILLIZ_URI", "ZILLIZ_USER_NAME", "ZILLIZ_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings()


def test_dirs_are_under_project_root():
    assert UPLOADS_DIR == DATA_DIR / "uploads"
    assert PROCESSED_DIR == DATA_DIR / "processed"
    assert DATA_DIR.name == "data"
