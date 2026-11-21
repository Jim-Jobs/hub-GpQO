"""任务 8.3a（openai-agents 套）：activity 单测 —— httpx 全 mock。

- 下游进程1/2 以 FastAPI 假服务 + ASGITransport 顶替（含 4xx/断流剧本）；
- WorkflowStreamClient 以 FakeStreamClient 顶替（activity 上下文外可运行），
  断言 respond activity 发布的 delta 事件序列与批次语义。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import activities
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse


def _fake_downstream() -> FastAPI:
    """假进程1/2：/analyze 与 /respond 按请求参数切剧本。"""
    app = FastAPI()

    @app.post("/analyze")
    async def analyze(request: dict) -> dict:
        if request["text"] == "触发4xx":
            raise HTTPException(status_code=400, detail="bad")
        if request["text"] == "触发5xx":
            raise HTTPException(status_code=503, detail="down")
        return {"label": "negative", "reason": "假识别依据"}

    @app.post("/respond")
    async def respond(request: dict) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            if request["text"] == "触发断流":
                yield 'data: {"delta": "只发一段"}\n\n'
                return  # 无 done 即断流
            for tok in ["别难过", "，", "会好的"]:
                yield f'data: {json.dumps({"delta": tok}, ensure_ascii=False)}\n\n'
            yield 'data: {"done": true}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


class _FakeTopic:
    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event, **_: object) -> None:
        self.events.append(event)


class _FakeStreamClient:
    """记录 publish 事件的 WorkflowStreamClient 替身。"""

    topic_handle = _FakeTopic()

    def __init__(self, **_: object) -> None:
        pass

    @classmethod
    def from_within_activity(cls, **kw: object) -> _FakeStreamClient:
        return cls(**kw)

    def topic(self, name: str, type=None):
        return self.topic_handle

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture()
def mock_downstream(monkeypatch):
    """httpx.AsyncClient -> 绑定假下游的 ASGITransport client。"""
    transport = httpx.ASGITransport(app=_fake_downstream())
    real_async_client = httpx.AsyncClient  # monkeypatch 前保存原构造器

    def factory(**kw: object) -> httpx.AsyncClient:
        kw.pop("transport", None)  # 防与假 transport 重复
        return real_async_client(transport=transport, **kw)

    monkeypatch.setattr(activities.httpx, "AsyncClient", factory)
    monkeypatch.setattr(activities.activity, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(activities, "WorkflowStreamClient", _FakeStreamClient)
    _FakeStreamClient.topic_handle = _FakeTopic()
    return _FakeStreamClient.topic_handle


async def test_analyze_activity_ok(mock_downstream):
    result = await activities.analyze_activity("我心情不好", [{"role": "user", "content": "上文"}])
    assert result == {"label": "negative", "reason": "假识别依据"}


async def test_analyze_activity_4xx_non_retryable(mock_downstream):
    from activities import PermanentHttpError

    with pytest.raises(PermanentHttpError):
        await activities.analyze_activity("触发4xx", [])


async def test_analyze_activity_transient(mock_downstream):
    """进程1 服务错误（5xx）→ RuntimeError（瞬时，可重试类；连接拒绝同语义）。"""
    with pytest.raises(RuntimeError, match="服务错误"):
        await activities.analyze_activity("触发5xx", [])


async def test_respond_activity_publishes_deltas(mock_downstream):
    topic = mock_downstream
    full = await activities.respond_activity("我心情不好", "negative", "r", [])
    assert full == "别难过，会好的"
    published = [e.text for e in topic.events if e.type == "delta"]
    assert "".join(published) == full  # delta 拼接完整性
    assert all(e.type == "delta" for e in topic.events)


async def test_respond_activity_broken_stream(mock_downstream):
    """进程2 中途断流（无 done）→ RuntimeError。"""
    with pytest.raises(RuntimeError, match="断流"):
        await activities.respond_activity("触发断流", "negative", "r", [])
