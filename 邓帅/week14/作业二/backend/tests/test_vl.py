"""VL 消息组装单测（不调模型，monkeypatch settings 的图片上限）。"""

import base64

import pytest

from mrag.api.vl import build_vl_messages, read_image_as_data_url


@pytest.fixture
def fake_processed(tmp_path, monkeypatch):
    """把 PROCESSED_DIR 指到 tmp_path，并放一张真图片。"""
    import mrag.api.vl as vl
    import mrag.config as cfg

    img = tmp_path / "doc" / "images" / "f.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\xff\xd8fakejpg")
    monkeypatch.setattr(cfg, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(vl, "PROCESSED_DIR", tmp_path)
    yield tmp_path, img


def test_basic_text_only():
    msgs = build_vl_messages([], "讲了啥？", [{"file_name": "a.pdf", "text": "内容"}])
    assert msgs[0]["role"] == "system"
    user = msgs[-1]
    assert user["role"] == "user"
    assert isinstance(user["content"], list)
    assert user["content"][0]["type"] == "text"
    assert "a.pdf" in user["content"][0]["text"]
    assert "讲了啥" in user["content"][0]["text"]
    assert all(c["type"] == "text" for c in user["content"])  # 无图


def test_empty_context_declared():
    msgs = build_vl_messages([], "q", [])
    assert "暂无命中" in msgs[-1]["content"][0]["text"]


def test_user_image_included():
    msgs = build_vl_messages([], "q", [], user_image_b64="QUJD")
    urls = [c for c in msgs[-1]["content"] if c["type"] == "image_url"]
    assert len(urls) == 1
    assert urls[0]["image_url"]["url"].startswith("data:image/jpeg;base64,QUJD")


def test_context_images_capped(fake_processed, monkeypatch):
    root, img = fake_processed
    # URL 相对挂载根（root.parent）；挂载根下路径为 <root.name>/doc/images/f.jpg
    urls = [f"/files/{root.name}/doc/images/f.jpg"] * 5
    import mrag.api.vl as vl
    from mrag.config import Settings

    s = Settings()
    monkeypatch.setattr(vl, "get_settings", lambda: s)
    msgs = build_vl_messages([], "q", [], context_image_urls=urls)
    n = sum(1 for c in msgs[-1]["content"] if c["type"] == "image_url")
    assert n == s.vl_max_images == 2


def test_history_truncated():
    history = [{"role": "user", "content": f"第{i}轮"} for i in range(20)]
    msgs = build_vl_messages(history, "q", [])
    # system + 10 历史 + 当前 = 12
    assert len(msgs) == 12


def test_read_image_data_url(fake_processed):
    root, img = fake_processed
    url = read_image_as_data_url(img)
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\xff\xd8fakejpg"
    assert read_image_as_data_url(root / "nope.jpg") is None
