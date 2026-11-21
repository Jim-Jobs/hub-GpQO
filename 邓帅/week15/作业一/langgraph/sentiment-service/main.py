"""进程1 · 情感识别服务（langgraph 实现，:8102）。

复刻对照（openai-agents 版见 ../openai-agents/sentiment-service/）：
  Agent(output_type=SentimentResult) + Runner.run
    → StateGraph 单节点 + chat_model.with_structured_output(SentimentResult)

API 契约与 openai-agents 版完全一致（交叉替换基础）：
  POST /analyze {"text","history"} -> {"label","reason"}；GET /health。
服务无状态；LLM 客户端 max_retries=0（失败 5xx，重试交给调用方网关）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

# 共享契约位于套根 contracts.py（与 openai-agents 版同一定义）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import AnalyzeRequest, SentimentResult

load_dotenv()

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


class AnalyzeState(TypedDict):
    """单节点图状态：入参消息 -> 结构化识别结果。"""

    input_messages: list[dict[str, Any]]
    label: str
    reason: str


chat_model = ChatOpenAI(
    model=os.getenv("BASE_MODEL"),
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL"),
    max_retries=0,
)
structured_llm = chat_model.with_structured_output(SentimentResult)


async def analyze_node(state: AnalyzeState) -> dict[str, str]:
    """节点内做结构化识别（输出 schema 与 openai-agents 版同一 pydantic）。"""
    result = await structured_llm.ainvoke(state["input_messages"])
    return {"label": result.label, "reason": result.reason}


builder = StateGraph(AnalyzeState)
builder.add_node("analyze", analyze_node)
builder.add_edge(START, "analyze")
builder.add_edge("analyze", END)
graph = builder.compile()

app = FastAPI(title="Emotion Pipeline · 进程1 情感识别（langgraph）")


@app.post("/analyze", response_model=SentimentResult)
async def analyze(request: AnalyzeRequest) -> SentimentResult:
    """无状态情感识别：判断仅由本次请求的 text 与 history 决定。"""
    input_messages: list[dict[str, Any]] = [
        {"role": "system", "content": INSTRUCTIONS},
        *[m.model_dump() for m in request.history],
        {"role": "user", "content": request.text},
    ]
    result = await graph.ainvoke({"input_messages": input_messages})
    return SentimentResult(label=result["label"], reason=result["reason"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
