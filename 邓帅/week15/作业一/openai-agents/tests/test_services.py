"""任务 8.1（openai-agents 套）：进程1/2 单测 —— LLM 全 mock。

- 进程1：mock Runner.run 固定 label，断言 /analyze 契约与多轮 history 透传；
- 进程2：mock Runner.run_streamed 产假 token 事件流，断言 SSE 序列
  （delta 拼接完整性、reasoning 过滤、done 恰好一条）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

SUITE = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, SUITE / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sentiment_main():
    return load_module("oa_sentiment_main", "sentiment-service/main.py")


@pytest.fixture(scope="module")
def respond_main():
    return load_module("oa_respond_main", "respond-service/main.py")


def _event(data_type: str, delta: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        type="raw_response_event",
        data=types.SimpleNamespace(type=data_type, delta=delta),
    )


class _FakeRunResult:
    def __init__(self, final_output):
        self.final_output = final_output


async def test_analyze_contract(monkeypatch, sentiment_main):
    """固定 label：/analyze 返回契约字段；history 透传给 Runner。"""
    from contracts import SentimentResult

    captured = {}

    async def fake_run(agent, input):
        captured["input"] = input
        return _FakeRunResult(SentimentResult(label="positive", reason="测试依据"))

    monkeypatch.setattr(sentiment_main.Runner, "run", fake_run)

    transport = httpx.ASGITransport(app=sentiment_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/analyze",
            json={
                "text": "今天很开心",
                "history": [{"role": "user", "content": "上文"}],
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body == {"label": "positive", "reason": "测试依据"}

    # history 与最新输入拼成消息数组，末位是本轮 user
    msgs = captured["input"]
    assert msgs[0] == {"role": "user", "content": "上文"}
    assert msgs[-1] == {"role": "user", "content": "今天很开心"}


async def test_analyze_rejects_empty_text(sentiment_main):
    """pydantic 契约：空 text 422。"""
    transport = httpx.ASGITransport(app=sentiment_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/analyze", json={"text": "", "history": []})
    assert r.status_code == 422


class _FakeStream:
    def __init__(self):
        self.final_output = "你好，世界"

    async def stream_events(self):
        # reasoning 思考流必须被过滤；正文 delta 逐条透出
        yield _event("response.reasoning_text.delta", "内心思考")
        for token in ["你好", "，", "世界"]:
            yield _event("response.output_text.delta", token)
        yield _event("response.output_text.done", "")


async def test_respond_sse_sequence(monkeypatch, respond_main):
    """假 token 流：delta 拼接完整、reasoning 过滤、done 恰好一条。"""
    monkeypatch.setattr(
        respond_main.Runner, "run_streamed", lambda agent, input: _FakeStream()
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
            assert resp.headers["content-type"].startswith("text/event-stream")
            deltas, dones = [], 0
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if "delta" in payload:
                        deltas.append(payload["delta"])
                    elif payload.get("done"):
                        dones += 1
    assert "".join(deltas) == "你好，世界"
    assert dones == 1
