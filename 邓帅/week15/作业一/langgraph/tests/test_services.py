"""任务 8.1（langgraph 套）：进程1/2 单测 —— LLM 全 mock。

- 进程1：mock graph.ainvoke 固定 label，断言 /analyze 契约（与 OA 版一致）；
- 进程2：mock build_graph 返回假图（astream 产假 token chunk 流），
  断言 SSE 序列：delta 拼接完整性、done 恰好一条。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessageChunk

SUITE = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, SUITE / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sentiment_main():
    return load_module("lg_sentiment_main", "sentiment-service/main.py")


@pytest.fixture(scope="module")
def respond_main():
    return load_module("lg_respond_main", "respond-service/main.py")


async def test_analyze_contract(monkeypatch, sentiment_main):
    """固定 label：/analyze 返回契约字段；system prompt + history 拼装。"""
    captured = {}

    async def fake_ainvoke(input, config=None, **_: object):
        captured["input"] = input
        return {"label": "neutral", "reason": "假依据"}

    monkeypatch.setattr(sentiment_main.graph, "ainvoke", fake_ainvoke)

    transport = httpx.ASGITransport(app=sentiment_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/analyze",
            json={
                "text": "明天开会",
                "history": [{"role": "user", "content": "上文"}],
            },
        )
    assert r.status_code == 200
    assert r.json() == {"label": "neutral", "reason": "假依据"}

    msgs = captured["input"]["input_messages"]
    assert msgs[0]["role"] == "system"  # 三分类指令在首位
    assert msgs[-1] == {"role": "user", "content": "明天开会"}


async def test_analyze_rejects_empty_text(sentiment_main):
    transport = httpx.ASGITransport(app=sentiment_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/analyze", json={"text": "", "history": []})
    assert r.status_code == 422


class _FakeGraph:
    def __init__(self, tokens: list[str]):
        self._tokens = tokens

    async def astream(self, input, config=None, stream_mode=None, **_: object):
        for tok in self._tokens:
            # 与真实 astream("messages") 同构：(AIMessageChunk, metadata)
            yield AIMessageChunk(content=tok), {"langgraph_node": "respond"}


async def test_respond_sse_sequence(monkeypatch, respond_main):
    """假 token 流：delta 拼接完整、done 恰好一条（与 OA 版 SSE 格式一致）。"""
    monkeypatch.setattr(
        respond_main, "build_graph", lambda label, reason: _FakeGraph(["加油", "，", "朋友"])
    )

    transport = httpx.ASGITransport(app=respond_main.app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://t") as c,
        c.stream(
            "POST",
            "/respond",
            json={"text": "hi", "label": "positive", "reason": "r", "history": []},
        ) as resp,
    ):
            assert resp.status_code == 200
            deltas, dones = [], 0
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if "delta" in payload:
                        deltas.append(payload["delta"])
                    elif payload.get("done"):
                        dones += 1
    assert "".join(deltas) == "加油，朋友"
    assert dones == 1
