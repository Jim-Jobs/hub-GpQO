"""Temporal activities：进程1/2 的 HTTP 封装（worker 进程内执行）。

重试语义（与 workflow 侧 RetryPolicy 对齐，见 workflows.py）：
- 瞬时错误（连接失败/超时/5xx/SSE 断流）→ 抛 RuntimeError，交给
  Temporal 按 RetryPolicy 自动重试；
- 永久错误（下游 4xx/契约非法）→ 抛 PermanentHttpError，列入
  non_retryable_error_types，不做无意义重试；
- respond activity 逐 delta heartbeat（感知取消），token 增量经
  Workflow Streams 以 200ms 批次 publish。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from typing import Any

import httpx
from temporalio import activity
from temporalio.contrib.workflow_streams import WorkflowStreamClient

# 套根共享模块
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from streams import EVENT_TOPIC, StreamEvent

SENTIMENT_URL = os.getenv("SENTIMENT_URL", "http://127.0.0.1:8002")
RESPOND_URL = os.getenv("RESPOND_URL", "http://127.0.0.1:8003")
#: Workflow Streams 批次间隔（design.md：约 200ms）
BATCH_INTERVAL = timedelta(milliseconds=200)

VALID_LABELS = ("positive", "negative", "neutral")

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
RESPOND_TIMEOUT = httpx.Timeout(120.0, connect=5.0)  # 与 StartToClose 120s 对齐


class PermanentHttpError(Exception):
    """下游永久错误（4xx/契约非法），不重试。"""


@activity.defn
async def analyze_activity(
    text: str, history: list[dict[str, Any]]
) -> dict[str, str]:
    """调进程1 POST /analyze，返回 {"label","reason"}。"""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{SENTIMENT_URL}/analyze",
                json={"text": text, "history": history},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"进程1 调用失败（瞬时）: {exc}") from exc

    if resp.status_code >= 400:
        if resp.status_code < 500:
            raise PermanentHttpError(
                f"进程1 永久错误 {resp.status_code}: {resp.text[:200]}"
            )
        raise RuntimeError(f"进程1 服务错误 {resp.status_code}")

    result = resp.json()
    if result.get("label") not in VALID_LABELS:
        raise PermanentHttpError(f"进程1 返回非法 label: {result.get('label')!r}")
    if not result.get("reason"):
        raise PermanentHttpError("进程1 返回空 reason")
    return {"label": result["label"], "reason": result["reason"]}


@activity.defn
async def respond_activity(
    text: str, label: str, reason: str, history: list[dict[str, Any]]
) -> str:
    """流式调进程2 POST /respond：token 经 Workflow Streams 发布，返回全文。

    下游 SSE 以 {"done": true} 显式收尾；未见 done 即视为断流（瞬时，
    可重试），已收到的 delta 已随批次发布出去（网关/前端保留展示）。
    """
    stream_client = WorkflowStreamClient.from_within_activity(
        batch_interval=BATCH_INTERVAL
    )
    topic = stream_client.topic(EVENT_TOPIC, type=StreamEvent)
    parts: list[str] = []

    # 退出时 stream_client 自动 flush 余量
    async with (
        stream_client,
        httpx.AsyncClient(timeout=RESPOND_TIMEOUT) as client,
        client.stream(
            "POST",
            f"{RESPOND_URL}/respond",
            json={"text": text, "label": label, "reason": reason, "history": history},
        ) as resp,
    ):
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    if resp.status_code < 500:
                        raise PermanentHttpError(
                            f"进程2 永久错误 {resp.status_code}: {body[:200]}"
                        )
                    raise RuntimeError(f"进程2 服务错误 {resp.status_code}")

                done_seen = False
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if payload.get("delta"):
                        topic.publish(StreamEvent(type="delta", text=payload["delta"]))
                        parts.append(payload["delta"])
                        activity.heartbeat()  # 逐批心跳，感知取消
                    elif payload.get("done"):
                        done_seen = True
                if not done_seen:
                    raise RuntimeError("进程2 SSE 中途断流（未收到 done 标记）")

    return "".join(parts)
