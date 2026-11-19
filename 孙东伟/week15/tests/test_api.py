"""接口层测试：进程串联、参数校验、引擎切换。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert set(body["engines"]) == {"langgraph", "agents"}


def test_chat_with_each_engine() -> None:
    with TestClient(app) as client:
        for engine in ("langgraph", "agents"):
            resp = client.post(
                "/api/chat",
                json={"text": "今天升职了，特别开心！", "engine": engine},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["engine"] == engine
            assert body["sentiment"]["label"] == "positive"
            assert body["reply"]
            assert body["latency_ms"] >= 0
            assert len(body["trace_id"]) == 12


def test_crisis_short_circuit() -> None:
    with TestClient(app) as client:
        body = client.post("/api/chat", json={"text": "我不想活了"}).json()
    assert body["crisis"] is True
    assert "12356" in body["reply"]


def test_unknown_engine_returns_400() -> None:
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"text": "你好", "engine": "langchain"})
    assert resp.status_code in (400, 422)


def test_empty_text_rejected() -> None:
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"text": ""}).status_code == 422
