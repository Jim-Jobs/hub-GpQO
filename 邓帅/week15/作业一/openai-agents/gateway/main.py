"""网关（:8001）：FastAPI + Temporal client + SQLiteSession + SSE 下发。

每轮 /api/chat = 一次 EmotionPipelineWorkflow 执行：
  ① SQLiteSession 按 thread_id 取历史（无则生成新 id）
  ② start_workflow + 订阅 Workflow Streams（from_offset=0，durable log
     回放保证不丢事件）
  ③ 事件翻译成 SSE 下发：meta → sentiment → delta* → done
  ④ 成功后把本轮 user/assistant 消息写回 session；任一环节失败以
     error 事件收尾（不出 5xx，spec：下游失败降级）
  ⑤ workflow_id 含 thread_id 与轮次（run_id 可追溯，Temporal UI 对账）

启动：uvicorn main:app --port 8001（scripts/start_all.sh 拉起）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from agents.memory.sqlite_session import SQLiteSession
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from temporalio.client import Client
from temporalio.contrib.workflow_streams import WorkflowStreamClient

# 套根共享模块 + worker 的 workflow 定义
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from contracts import ChatRequest
from streams import EVENT_TOPIC, StreamEvent
from workflows import EmotionPipelineWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "emotion-oa")
BATCH_INTERVAL = timedelta(milliseconds=200)  # 与 worker 一致

DATA_DIR = Path(__file__).resolve().parent / "data"
SESSIONS_DB = DATA_DIR / "sessions.sqlite"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gateway")


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _history_from_items(items: list[Any]) -> list[dict[str, str]]:
    """SQLiteSession items -> 下游契约 history（只保留 user/assistant 文本）。"""
    history: list[dict[str, str]] = []
    for item in items:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            history.append({"role": role, "content": content})
    return history


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(exist_ok=True)
    app.state.temporal = await Client.connect(TEMPORAL_ADDRESS)
    logger.info("Temporal client 已连接 %s (task_queue=%s)", TEMPORAL_ADDRESS, TASK_QUEUE)
    yield


app = FastAPI(title="Emotion Pipeline · 网关（openai-agents + Temporal）", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _chat_events(
    temporal: Client, text: str, thread_id: str
) -> AsyncIterator[dict[str, str]]:
    session = SQLiteSession(thread_id, db_path=str(SESSIONS_DB))
    history = _history_from_items(await session.get_items())
    turn = len(history) // 2 + 1
    # thread_id + 轮次入 workflow_id：run_id 与会话可追溯（spec）
    workflow_id = f"emotion-{thread_id[:12]}-t{turn:03d}-{uuid.uuid4().hex[:6]}"

    yield {"event": "meta", "data": _dump({"thread_id": thread_id})}

    try:
        handle = await temporal.start_workflow(
            EmotionPipelineWorkflow.run,
            args=[text, history],
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        logger.info("workflow 已启动 thread=%s workflow_id=%s", thread_id, workflow_id)

        stream_client = WorkflowStreamClient.create(
            temporal, handle.id, batch_interval=BATCH_INTERVAL
        )
        async for item in stream_client.subscribe(
            [EVENT_TOPIC], result_type=StreamEvent
        ):
            ev: StreamEvent = item.data
            if ev.type == "sentiment":
                yield {
                    "event": "sentiment",
                    "data": _dump({"label": ev.label, "reason": ev.reason}),
                }
            elif ev.type == "delta":
                yield {"event": "delta", "data": _dump({"text": ev.text})}

        result = await handle.result()

        # 轮末写回会话历史（仅成功轮次）
        await session.add_items(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": result["response"]},
            ]
        )
        logger.info(
            "workflow 完成 thread=%s label=%s chars=%d",
            thread_id,
            result["label"],
            len(result["response"]),
        )
        yield {"event": "done", "data": "{}"}
    except Exception as exc:  # noqa: BLE001 — SSE 连接内异常以事件形式落地
        logger.warning("轮次失败 thread=%s: %r", thread_id, exc)
        # 展开异常链到根因（如 ActivityError → RuntimeError"进程2 调用失败…"）
        cur: BaseException = exc
        for _ in range(5):
            if cur.__cause__ is None:
                break
            cur = cur.__cause__
        message = str(cur) or str(exc)
        yield {"event": "error", "data": _dump({"message": f"编排失败：{message}"})}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> EventSourceResponse:
    thread_id = request.thread_id or uuid.uuid4().hex
    return EventSourceResponse(
        _chat_events(app.state.temporal, request.text, thread_id)
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
