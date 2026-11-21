"""网关（:8101）：网关本身是 StateGraph（in-memory graph orchestration）。

复刻对照（openai-agents 版见 ../openai-agents/gateway/）：
  Temporal durable orchestration（workflow + activities + Workflow Streams）
    → StateGraph 直连：analyze 节点（httpx 调进程1）→ 条件边 → respond
      节点（httpx 流式调进程2），节点内 get_stream_writer() 把远端 token
      写入 custom stream，端点以 astream(["custom","updates"]) 双模式消费。

会话：AsyncSqliteSaver checkpointer + thread_id（week13 模式）——与
openai-agents 套的 SQLiteSession 形成两种持久化哲学对照。
SSE 事件序列、错误降级、/health、81xx 环境变量默认值与 OA 版对齐。

启动：uvicorn main:app --port 8101（scripts/start_all.sh 拉起）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, TypedDict

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import ChatRequest

SENTIMENT_URL = os.getenv("SENTIMENT_URL", "http://127.0.0.1:8102")
RESPOND_URL = os.getenv("RESPOND_URL", "http://127.0.0.1:8103")

DATA_DIR = Path(__file__).resolve().parent / "data"
CHECKPOINT_DB = DATA_DIR / "checkpoints.sqlite"

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
RESPOND_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

VALID_LABELS = ("positive", "negative", "neutral")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gateway-lg")


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


class PipelineState(TypedDict):
    """网关图状态：messages 经 add_messages 累积，由 checkpointer 按
    thread_id 持久化；label/reason 为本轮识别结果（不跨轮）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    label: str
    reason: str


def _messages_to_history(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """checkpointer 消息 -> 下游契约 history（只保留 user/assistant 文本）。"""
    history: list[dict[str, str]] = []
    for m in messages:
        role = "user" if isinstance(m, HumanMessage) else (
            "assistant" if isinstance(m, AIMessage) else None
        )
        if role and isinstance(m.content, str) and m.content:
            history.append({"role": role, "content": m.content})
    return history


async def analyze_node(state: PipelineState) -> dict[str, str]:
    """调进程1 /analyze；历史取自 checkpointer 恢复的 messages。"""
    history = _messages_to_history(state["messages"][:-1])  # 末位是本轮输入
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{SENTIMENT_URL}/analyze",
            json={"text": state["messages"][-1].content, "history": history},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"进程1 情感识别失败：HTTP {resp.status_code}")
    result = resp.json()
    if result.get("label") not in VALID_LABELS:
        raise RuntimeError(f"进程1 返回非法 label：{result.get('label')!r}")
    return {"label": result["label"], "reason": result.get("reason", "")}


def route_by_label(state: PipelineState) -> str:
    """条件边：三类 label 都进 respond 节点（策略分支在进程2 内部）。"""
    return "respond"


async def respond_node(state: PipelineState) -> dict:
    """流式调进程2 /respond：token 写 custom stream，全文写回 messages。"""
    writer = get_stream_writer()
    text = state["messages"][-1].content
    history = _messages_to_history(state["messages"][:-1])
    parts: list[str] = []
    async with httpx.AsyncClient(timeout=RESPOND_TIMEOUT) as client, client.stream(
        "POST",
        f"{RESPOND_URL}/respond",
        json={
            "text": text,
            "label": state["label"],
            "reason": state["reason"],
            "history": history,
        },
    ) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"进程2 情感回应失败：HTTP {resp.status_code}")
        done_seen = False
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if payload.get("delta"):
                writer({"type": "delta", "text": payload["delta"]})
                parts.append(payload["delta"])
            elif payload.get("done"):
                done_seen = True
        if not done_seen:
            raise RuntimeError("进程2 SSE 中途断流（未收到 done 标记）")
    return {"messages": [AIMessage("".join(parts))]}


def build_graph(checkpointer: AsyncSqliteSaver):
    builder = StateGraph(PipelineState)
    builder.add_node("analyze", analyze_node)
    builder.add_node("respond", respond_node)
    builder.add_edge(START, "analyze")
    builder.add_conditional_edges("analyze", route_by_label, ["respond"])
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:
        app.state.graph = build_graph(checkpointer)
        logger.info("网关图已编译（checkpointer=%s）", CHECKPOINT_DB)
        yield


app = FastAPI(title="Emotion Pipeline · 网关（langgraph StateGraph）", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _chat_events(graph, text: str, thread_id: str) -> AsyncIterator[dict[str, str]]:
    yield {"event": "meta", "data": _dump({"thread_id": thread_id})}

    try:
        async for mode, chunk in graph.astream(
            {"messages": [HumanMessage(text)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom" and chunk.get("type") == "delta":
                yield {"event": "delta", "data": _dump({"text": chunk["text"]})}
            elif mode == "updates" and "analyze" in chunk:
                result = chunk["analyze"]
                yield {
                    "event": "sentiment",
                    "data": _dump({"label": result["label"], "reason": result["reason"]}),
                }
        logger.info("轮次完成 thread=%s", thread_id)
        yield {"event": "done", "data": "{}"}
    except Exception as exc:  # noqa: BLE001 — SSE 连接内异常以事件形式落地
        logger.warning("轮次失败 thread=%s: %r", thread_id, exc)
        yield {"event": "error", "data": _dump({"message": str(exc)})}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> EventSourceResponse:
    thread_id = request.thread_id or uuid.uuid4().hex
    return EventSourceResponse(
        _chat_events(app.state.graph, request.text, thread_id)
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
