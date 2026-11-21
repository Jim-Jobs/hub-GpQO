"""进程1 · 情感识别服务（openai-agents 实现，:8002）。

- `Agent(name="情感识别", output_type=SentimentResult)` + `Runner.run`：
  LLM 结构化输出三分类 + 一句话依据；
- 无状态：history 由网关每轮携带，服务不落任何会话数据；
- OpenAI 客户端 `max_retries=0`：失败直接 5xx，重试统一交给 Temporal
  activity 的 RetryPolicy（见 worker/）。

契约：POST /analyze {"text","history"} -> {"label","reason"}；GET /health。
启动：uvicorn main:app --port 8002（由 scripts/start_all.sh 拉起）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from dotenv import load_dotenv
from fastapi import FastAPI
from openai import AsyncOpenAI

# 共享契约位于套根 contracts.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import AnalyzeRequest, SentimentResult

load_dotenv()  # 向上找到 badou2026/.env

INSTRUCTIONS = """\
你是情感识别引擎，对用户最新一条输入做三分类情感判断：

- positive：开心、自豪、兴奋、感激等正向情绪
- negative：沮丧、焦虑、愤怒、疲惫、失落等负向情绪
- neutral：陈述事实、提问、安排事务，无明显情绪倾向

判断规则：
1. 以最新输入为主，但必须结合此前多轮对话上下文——
   例如"又来了"单独看情绪不明，在"连续加班一周"的语境下应判 negative；
2. 情绪不明显时倾向 neutral，不过度解读；
3. reason 用一句话中文说明判断依据，点出关键情绪线索（可引用上文）。
"""

app = FastAPI(title="Emotion Pipeline · 进程1 情感识别（openai-agents）")

# 自定义 base_url 前置（已知坑）：换默认客户端 + 关 tracing，见 design.md §8
set_default_openai_client(
    AsyncOpenAI(
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
        max_retries=0,
    )
)
set_tracing_disabled(True)

sentiment_agent = Agent(
    name="情感识别",
    instructions=INSTRUCTIONS,
    model=os.getenv("BASE_MODEL"),
    output_type=SentimentResult,
)


@app.post("/analyze", response_model=SentimentResult)
async def analyze(request: AnalyzeRequest) -> SentimentResult:
    """无状态情感识别：判断仅由本次请求的 text 与 history 决定。"""
    # openai-agents 的输入即 OpenAI 消息数组；history + 最新 user 消息
    input_messages: list[dict[str, Any]] = [m.model_dump() for m in request.history]
    input_messages.append({"role": "user", "content": request.text})

    result = await Runner.run(sentiment_agent, input_messages)
    return result.final_output


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
