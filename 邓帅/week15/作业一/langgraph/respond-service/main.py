"""进程2 · 情感回应服务（langgraph 实现，:8103）。

复刻对照（openai-agents 版见 ../openai-agents/respond-service/）：
  三角色 prompt + Runner.run_streamed 的 raw token 流
    → 单节点 StateGraph + `astream(stream_mode="messages")`：
    节点内 await llm.ainvoke(...)，token chunk 由 "messages" 模式
    经 langchain callback 透出，端点逐条转 SSE delta。

SSE 事件格式与 openai-agents 版完全一致：
  data: {"delta": "..."} 逐条，结尾恰好一条 {"done": true}；GET /health。
无状态；三角色分支在本进程内部（网关只透传 label）。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_core.messages import AIMessageChunk, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import RespondRequest

load_dotenv()

# 三类角色的回应策略（与 openai-agents 版保持同一定义）
ROLE_PROMPTS: dict[str, str] = {
    "positive": (
        "你是温暖的伙伴。用户正处于积极情绪中，请真诚地为用户高兴："
        "祝贺他的好消息，肯定他背后的付出与努力，放大这份喜悦，"
        "并轻轻展望未来。语气热情自然，不说教、不敷衍。"
    ),
    "negative": (
        "你是善解人意的倾听者。用户正处于消极情绪中，请先充分共情："
        "承认他的情绪是合理的、可以被理解的；再温和地帮他排解——"
        "给出宽慰的视角或一个小小的可行建议。不要说教，"
        "不要无视情绪直接甩建议，不要否定他的感受。"
    ),
    "neutral": (
        "你是平和自然的对话者。用户当前无明显情绪倾向，"
        "请围绕他正在谈论的话题自然延续对话：补充相关信息、"
        "顺着话题提问或给出实用回应。语气平和，"
        "不强行套用鼓励或安慰话术。"
    ),
}

RESPOND_RULES = """\
回应要求：
- 使用简体中文，2~4 句话，口语化、有温度；
- 紧扣用户最新这条输入的具体内容，可结合此前对话上下文保持连贯；
- 情感识别依据（仅供参考，不要复述）：{reason}
"""

chat_model = ChatOpenAI(
    model=os.getenv("BASE_MODEL"),
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL"),
    max_retries=0,
)


def build_graph(label: str, reason: str):
    """按本轮 label 组装角色 prompt 并编译单节点图（每请求建图，服务无状态）。"""
    instructions = ROLE_PROMPTS[label] + "\n" + RESPOND_RULES.format(reason=reason)

    async def respond_node(state: MessagesState) -> dict:
        # 节点内普通 ainvoke；token 流由外层 astream(stream_mode="messages")
        # 经 langchain callback 透出
        response = await chat_model.ainvoke(
            [SystemMessage(content=instructions), *state["messages"]]
        )
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond_node)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile()


app = FastAPI(title="Emotion Pipeline · 进程2 情感回应（langgraph）")


async def _respond_stream(request: RespondRequest) -> AsyncIterator[dict[str, str]]:
    """把 graph.astream(messages 模式) 的 token chunk 翻译成 SSE 事件流。"""
    input_messages: list[dict[str, Any]] = [m.model_dump() for m in request.history]
    input_messages.append({"role": "user", "content": request.text})

    graph = build_graph(request.label, request.reason)
    async for chunk, _metadata in graph.astream(
        {"messages": input_messages}, stream_mode="messages"
    ):
        # 仅转发正文 token（qwen 推理模型的 reasoning 不进 content，天然过滤）
        if isinstance(chunk, AIMessageChunk):
            text = chunk.content
            if isinstance(text, str) and text:
                yield {
                    "event": "delta",
                    "data": json.dumps({"delta": text}, ensure_ascii=False),
                }
    yield {"event": "done", "data": json.dumps({"done": True})}


@app.post("/respond")
async def respond(request: RespondRequest) -> EventSourceResponse:
    return EventSourceResponse(_respond_stream(request))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
