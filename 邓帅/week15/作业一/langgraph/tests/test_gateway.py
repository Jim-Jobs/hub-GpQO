"""任务 8.2（langgraph 套）：网关集成测 —— httpx mock 两个下游。

断言：
- SSE 事件序列 meta → sentiment → delta* → done；
- history 传递：第二轮 /analyze 收到第一轮 user/assistant 历史（checkpointer）；
- 错误降级：进程1 失败 → error 收尾无 delta；进程2 中途断流 →
  error 收尾且已收到的 delta 保留（无 5xx）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator as AIter
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

SUITE = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, SUITE / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_downstream(analyze_requests: list, respond_script: str = "ok") -> FastAPI:
    """假进程1/2：记录 analyze 请求体；respond 按剧本出 SSE。"""
    app = FastAPI()

    @app.post("/analyze")
    async def analyze(request: dict) -> dict:
        analyze_requests.append(request)
        if request["text"] == "进程1挂了":
            raise HTTPException(status_code=503, detail="down")
        return {"label": "negative", "reason": "假识别依据"}

    @app.post("/respond")
    async def respond(request: dict) -> StreamingResponse:
        async def gen() -> AIter[str]:
            if respond_script == "断流":
                yield 'data: {"delta": "只发了两段就断"}\n\n'
                yield 'data: {"delta": "…"}\n\n'
                return  # 无 done
            for tok in ["别难过", "，", "会好的"]:
                yield f'data: {json.dumps({"delta": tok}, ensure_ascii=False)}\n\n'
            yield 'data: {"done": true}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture()
async def gateway_app(monkeypatch, tmp_path):
    """装配假下游 + 内存网关（checkpointer 落 tmp 文件）。"""
    analyze_requests: list = []
    transport = httpx.ASGITransport(app=_fake_downstream(analyze_requests))
    real_async_client = httpx.AsyncClient

    def factory(**kw: object) -> httpx.AsyncClient:
        # 测试自备 transport 的 client 放行；网关内部调用（无 transport）
        # 拦到假下游
        if kw.get("transport") is not None:
            return real_async_client(**kw)
        return real_async_client(transport=transport, **kw)

    gw = load_module("lg_gateway_main", "gateway/main.py")
    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as cp:
        gw.app.state.graph = gw.build_graph(cp)
        gw.app.state._analyze_requests = analyze_requests
        yield gw.app


async def _chat(c: httpx.AsyncClient, text: str, tid: str | None = None):
    events: list[tuple[str, dict]] = []
    async with c.stream("POST", "/api/chat", json={"text": text, "thread_id": tid}) as resp:
        assert resp.status_code == 200  # 降级也走 SSE，不出 5xx
        cur = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                cur = line[6:].strip()
            elif line.startswith("data:") and cur:
                events.append((cur, json.loads(line[5:])))
    return events


async def test_sse_sequence_and_history(gateway_app):
    """正常两轮：事件序列正确；第二轮 analyze 收到第一轮历史。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://t"
    ) as c:
        ev1 = await _chat(c, "我心情不好")
        kinds1 = [e for e, _ in ev1]
        assert kinds1[0] == "meta"
        assert kinds1[1] == "sentiment"
        assert kinds1[-1] == "done"
        deltas = [d["text"] for e, d in ev1 if e == "delta"]
        assert "".join(deltas) == "别难过，会好的"

        tid = dict(ev1)["meta"]["thread_id"]
        await _chat(c, "还是很难过", tid)

    reqs = gateway_app.state._analyze_requests
    assert len(reqs) == 2
    assert reqs[0]["history"] == []  # 首轮无历史
    assert reqs[1]["history"] == [
        {"role": "user", "content": "我心情不好"},
        {"role": "assistant", "content": "别难过，会好的"},
    ]


async def test_degrade_analyze_failure(gateway_app):
    """进程1 失败：meta → error，无 delta、无 sentiment。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://t"
    ) as c:
        events = await _chat(c, "进程1挂了")
    kinds = [e for e, _ in events]
    assert kinds == ["meta", "error"]
    assert "进程1" in dict(events)["error"]["message"]


async def test_degrade_respond_broken_stream(monkeypatch, tmp_path):
    """进程2 中途断流：sentiment + 已收 delta 保留，error 收尾。"""
    analyze_requests: list = []
    transport = httpx.ASGITransport(app=_fake_downstream(analyze_requests, "断流"))
    real_async_client = httpx.AsyncClient

    def factory(**kw: object) -> httpx.AsyncClient:
        if kw.get("transport") is not None:
            return real_async_client(**kw)
        return real_async_client(transport=transport, **kw)

    gw = load_module("lg_gateway_main_broken", "gateway/main.py")
    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp2.sqlite")) as cp:
        gw.app.state.graph = gw.build_graph(cp)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gw.app), base_url="http://t"
        ) as c:
            events = await _chat(c, "我心情不好")

    kinds = [e for e, _ in events]
    assert kinds[0] == "meta"
    assert kinds[1] == "sentiment"
    deltas = [d["text"] for e, d in events if e == "delta"]
    assert "".join(deltas) == "只发了两段就断…"  # 已收 delta 保留
    assert kinds[-1] == "error"
    assert "断流" in dict(events)["error"]["message"]
