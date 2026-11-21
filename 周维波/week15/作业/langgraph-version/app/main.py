"""后端进程：接收前端输入，调用 LangGraph 编排的 进程1 → 进程2。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .graph import get_graph, run_pipeline
from .schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="情绪支持流水线 · LangGraph 版",
    description="START → 情感识别节点 → 条件边路由 → 鼓励/排解/中性节点 → END",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", summary="健康检查")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "framework": "langgraph",
        "model": settings.model,
        "base_url": settings.base_url,
        "mock": settings.mock,
        "nodes": ["analyze", "encourage", "comfort", "neutral"],
    }


@app.get("/api/graph", summary="导出图结构（Mermaid），便于排查路由")
async def graph_structure() -> dict:
    settings = get_settings()
    if settings.mock:
        return {"mock": True, "mermaid": "未配置 API Key 时不构建 LLM 图"}
    try:
        return {"mermaid": get_graph().get_graph().draw_mermaid()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("导出图结构失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat", response_model=ChatResponse, summary="情感识别 + 分支回应")
async def chat(req: ChatRequest) -> ChatResponse:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="message 不能为空")
    try:
        return await run_pipeline(text, session_id=req.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("流水线执行失败")
        raise HTTPException(status_code=502, detail=f"流水线执行失败：{exc}") from exc
