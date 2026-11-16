"""pytest 公共夹具。

约定：单测**不依赖网络**、不依赖真实 ES / Kafka / MinerU / vLLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, get_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """与 .env 无关的测试用配置（`_env_file=None` 屏蔽本地 .env 干扰）。"""
    get_settings.cache_clear()
    return Settings(_env_file=None, mock_mode=True, data_dir=tmp_path)
