"""进程2 · 情感回应服务（openai-agents 实现，:8003）。

- 三角色 prompt 按 label 切换（positive 鼓励 / negative 排解 / neutral 平和），
  回应策略留在本进程内部，网关只透传 label（契约不泄漏策略）；
- `Runner.run_streamed` 逐 token 下发 SSE：`data: {"delta": ...}`，
  流末尾 `data: {"done": true}`（恰好一条）；
- 只转发 `response.output_text.delta`：qwen3.8-max 为推理模型，
  `response.reasoning_text.delta` 是思考过程，不得下发给用户；
- 无状态：text/label/reason/history 均由网关每轮携带。

契约：POST /respond（SSE 流）；GET /health。
启动：uvicorn main:app --port 8003（由 scripts/start_all.sh 拉起）。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from dotenv import load_dotenv
from fastapi import FastAPI
from openai import AsyncOpenAI
from sse_starlette.sse import EventSourceResponse

# 共享契约位于套根 contracts.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import RespondRequest

load_dotenv()

# 三类角色的回应策略（分支在本进程内部）
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

BASE_INSTRUCTIONS = """\
{role_prompt}

回应要求：
- 使用简体中文，2~4 句话，口语化、有温度；
- 紧扣用户最新这条输入的具体内容，可结合此前对话上下文保持连贯；
- 情感识别依据（仅供参考，不要复述）：{reason}
"""

app = FastAPI(title="Emotion Pipeline · 进程2 情感回应（openai-agents）")

set_default_openai_client(
    AsyncOpenAI(
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
        max_retries=0,
    )
)
set_tracing_disabled(True)

MODEL = os.getenv("BASE_MODEL")


async def _respond_stream(request: RespondRequest) -> AsyncIterator[dict[str, str]]:
    """把 Runner.run_streamed 的 token 翻译成 SSE 事件字典流。"""
    instructions = BASE_INSTRUCTIONS.format(
        role_prompt=ROLE_PROMPTS[request.label], reason=request.reason
    )
    agent = Agent(
        name=f"情感回应-{request.label}",
        instructions=instructions,
        model=MODEL,
    )
    input_messages: list[dict[str, Any]] = [m.model_dump() for m in request.history]
    input_messages.append({"role": "user", "content": request.text})

    stream = Runner.run_streamed(agent, input_messages)
    async for event in stream.stream_events():
        # 只转发正文 token；reasoning 思考流过滤
        if (
            event.type == "raw_response_event"
            and event.data.type == "response.output_text.delta"
            and event.data.delta
        ):
            yield {"event": "delta", "data": json.dumps({"delta": event.data.delta}, ensure_ascii=False)}
    yield {"event": "done", "data": json.dumps({"done": True})}


@app.post("/respond")
async def respond(request: RespondRequest) -> EventSourceResponse:
    return EventSourceResponse(_respond_stream(request))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
