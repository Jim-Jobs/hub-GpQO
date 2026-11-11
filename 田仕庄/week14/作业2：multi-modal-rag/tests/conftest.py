from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在导入 app.* 之前把数据目录指向测试目录（引擎在 import 时创建）
_BASE = Path(__file__).resolve().parent / ".runtime"
_BASE.mkdir(parents=True, exist_ok=True)
_SESSION_DATA = _BASE / uuid.uuid4().hex[:8]
_SESSION_DATA.mkdir()

os.environ["RAG_DATA_DIR"] = str(_SESSION_DATA)
os.environ["RAG_MOCK_EXTERNAL"] = "1"
os.environ["RAG_PARSE_BACKEND"] = "mock"
os.environ["RAG_LOG_LEVEL"] = "DEBUG"
os.environ["RAG_SWEEP_ENABLED"] = "0"
os.environ["RAG_PARSE_RETRIES"] = "0"
os.environ["RAG_WORKER_RETRY_BASE_SEC"] = "0"
os.environ["RAG_WORKER_POLL_INTERVAL_SEC"] = "0.01"


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir():
    """整个测试会话使用同一个临时数据目录，结束后清理。"""
    from app.config import get_settings

    get_settings.cache_clear()
    yield _SESSION_DATA

    get_settings.cache_clear()
    from app.db import engine
    from app.logging_setup import shutdown_logging

    shutdown_logging()
    engine.dispose()
    for _ in range(3):
        shutil.rmtree(_SESSION_DATA, ignore_errors=True)
        if not _SESSION_DATA.exists():
            break
        time.sleep(0.05)
    try:
        _BASE.rmdir()
    except OSError:
        pass


@pytest.fixture(autouse=True)
def clean_state(isolated_data_dir):
    """每个测试前清空文件/Key/入队记录，保证用例互不影响。"""
    from app.db import Session, init_db
    from app.ingest.queue import reset_mock_queue
    from app.models import File, KbApiKey

    init_db()
    with Session() as session:
        session.query(File).delete()
        session.query(KbApiKey).delete()
        session.commit()
    reset_mock_queue()
    yield


@pytest.fixture()
def client(isolated_data_dir):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
