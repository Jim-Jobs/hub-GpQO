from __future__ import annotations

import os
import time
from pathlib import Path


def test_cleanup_removes_only_expired_directories(isolated_data_dir):
    from app.config import get_settings
    from app.ingest.sweeper import cleanup_question_images

    settings = get_settings()
    old = Path(settings.questions_dir) / "old-trace"
    fresh = Path(settings.questions_dir) / "fresh-trace"
    old.mkdir(parents=True, exist_ok=True)
    fresh.mkdir(parents=True, exist_ok=True)
    (old / "img1.jpg").write_bytes(b"x")
    (fresh / "img1.jpg").write_bytes(b"x")

    expired = time.time() - 48 * 3600
    os.utime(old, (expired, expired))

    cleaned = cleanup_question_images(ttl_hours=24)

    assert cleaned == 1
    assert not old.exists()
    assert fresh.is_dir()


def test_cleanup_disabled_when_ttl_not_positive(isolated_data_dir):
    from app.config import get_settings
    from app.ingest.sweeper import cleanup_question_images

    settings = get_settings()
    directory = Path(settings.questions_dir) / "keep-trace"
    directory.mkdir(parents=True, exist_ok=True)
    os.utime(directory, (0, 0))

    assert cleanup_question_images(ttl_hours=0) == 0
    assert directory.is_dir()
