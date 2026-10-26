"""API 冒烟测试:SSE 问答流、历史列表、schema 概览(LLM mock,离线)。"""
import json
from collections import deque

import pytest
from fastapi.testclient import TestClient

from app import history
from app.main import app
from app.pipeline import steps


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """临时 history 库 + mock chat + 触发 lifespan(parser 初始化一次)。"""
    monkeypatch.setattr(history, "HISTORY_DB_PATH", tmp_path / "history.db")

    queue = deque([
        '["albums"]',
        "SELECT COUNT(*) AS n FROM albums",
        "共 347 张专辑。",
    ])

    async def fake_chat(prompt, system=None, temperature=0.2):
        return queue.popleft()

    monkeypatch.setattr(steps, "chat", fake_chat)

    with TestClient(app) as c:  # with 触发 lifespan(parser 缓存/ro 连接)
        yield c


def _parse_sse(text: str) -> list[dict]:
    """从 SSE 原文提取 data: 行的 JSON。"""
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_ask_streams_steps_and_final(client):
    with client.stream("POST", "/api/ask", json={"question": "多少张专辑?"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(chunk for chunk in resp.iter_text())
    events = _parse_sse(text)

    final = events[-1]
    assert final["event"] == "final" and final["status"] == "success"
    assert "COUNT" in final["sql"]
    done_steps = [e["step"] for e in events if e.get("status") == "done"]
    assert done_steps == [1, 2, 3, 4, 5, 6, 7]


def test_history_recorded_after_ask(client):
    client.post("/api/ask", json={"question": "多少张专辑?"})
    resp = client.get("/api/history")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["question"] == "多少张专辑?"
    assert items[0]["status"] == "success"
    assert items[0]["steps"]  # 各步摘要已随历史落库


def test_schema_endpoint(client):
    resp = client.get("/api/schema")
    data = resp.json()
    names = [t["name"] for t in data["tables"]]
    assert len(names) == 11
    assert "albums" in names
    assert data["relations"] > 0
