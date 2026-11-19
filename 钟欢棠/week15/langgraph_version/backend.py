"""LangGraph 版后端：接收前端输入，串联进程 1 和进程 2。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from common.config import settings
from common.schemas import ReplyResult
from common.web import build_index_page
from langgraph_version.graph import run_pipeline

app = FastAPI(title="LangGraph 情感响应服务")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.get("/api/health")
async def health() -> dict:
    cfg = settings()
    return {"status": "ok", "engine": "langgraph", "mode": "mock" if cfg.mock else "llm"}


@app.post("/api/chat", response_model=ReplyResult)
async def chat(req: ChatRequest) -> ReplyResult:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="输入内容不能为空")
    return await run_pipeline(message)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return build_index_page("LangGraph 情感响应助手")


if __name__ == "__main__":
    print(settings().describe())
    uvicorn.run(app, host="0.0.0.0", port=8001)
