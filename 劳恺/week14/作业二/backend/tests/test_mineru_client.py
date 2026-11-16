"""MinerU 适配器单测（不联网）。

覆盖实测踩到的坑：data_id 字符集、Zip Slip 防护、content_list 定位（要排除 v2）、
状态映射、错误码翻译。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.pipeline.mineru_client import (
    STATE_DONE,
    STATE_FAILED,
    STATE_RUNNING,
    MineruError,
    find_content_list,
    find_images,
    find_markdown,
    make_data_id,
    map_task_state,
    safe_extract,
    translate_error,
)


def test_make_data_id_keeps_valid_doc_id() -> None:
    assert make_data_id("d_ab12cd34ef56") == "d_ab12cd34ef56"


def test_make_data_id_strips_chinese_and_invalid_chars() -> None:
    """实测：data_id 只允许 [A-Za-z0-9_.-]，中文文件名不能直接用。"""
    data_id = make_data_id("财报-2024.pdf")
    assert data_id == "---2024.pdf"
    assert all(ch.isalnum() or ch in "_.-" for ch in data_id)


def test_make_data_id_truncates_to_128() -> None:
    assert len(make_data_id("a" * 300)) == 128


def test_make_data_id_never_empty() -> None:
    assert make_data_id("") == "doc"


def test_translate_known_error_codes() -> None:
    assert "Token" in translate_error("A0202")
    assert "200MB" in translate_error("-60005")
    assert "200 页" in translate_error("-60006")
    assert "读取文件超时" in translate_error("-60008")


def test_translate_unknown_code_falls_back_to_raw() -> None:
    message = translate_error("X999", "文件已加密")
    assert "文件已加密" in message


def test_map_state_running_with_progress() -> None:
    state = map_task_state(
        {
            "state": "running",
            "extract_progress": {"extracted_pages": 5, "total_pages": 10},
        }
    )
    assert state.state == STATE_RUNNING
    assert state.progress == 0.5


def test_map_state_done_is_full_progress() -> None:
    state = map_task_state({"state": "done", "full_zip_url": "https://x/a.zip"})
    assert state.state == STATE_DONE
    assert state.progress == 1.0
    assert state.zip_url == "https://x/a.zip"


def test_map_state_failed_carries_readable_error() -> None:
    state = map_task_state({"state": "failed", "err_code": "A0202", "err_msg": "bad token"})
    assert state.state == STATE_FAILED
    assert state.error and "Token" in state.error


def test_safe_extract_blocks_zip_slip(tmp_path: Path) -> None:
    """Zip Slip 防护：成员 resolve 后越出目标目录必须跳过。"""
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../evil.txt", "pwned")
        archive.writestr("images/ok.txt", "safe")

    dest = tmp_path / "dest"
    extracted = safe_extract(archive_path, dest)

    assert (dest / "images" / "ok.txt").is_file()
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()
    assert all(path.is_relative_to(dest.resolve()) for path in extracted)


def test_find_content_list_skips_v2(tmp_path: Path) -> None:
    """实测产物里同时有 v1 与 v2，必须取 v1（v2 是分页分组格式）。"""
    (tmp_path / "doc_content_list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "doc_content_list_v2.json").write_text("[]", encoding="utf-8")
    assert find_content_list(tmp_path).name == "doc_content_list.json"


def test_find_content_list_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MineruError, match="content_list"):
        find_content_list(tmp_path)


def test_find_markdown_and_images(tmp_path: Path) -> None:
    (tmp_path / "full.md").write_text("# 标题", encoding="utf-8")
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.jpg").write_bytes(b"x")
    (images / "b.jpg").write_bytes(b"y")

    assert find_markdown(tmp_path) == "# 标题"
    assert [path.name for path in find_images(tmp_path)] == ["a.jpg", "b.jpg"]


def test_find_markdown_returns_empty_when_absent(tmp_path: Path) -> None:
    assert find_markdown(tmp_path) == ""
