"""后端进程：接收前端输入，串联进程 1 与进程 2，返回统一 JSON。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .pipeline import run_pipeline
from .schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="情绪支持流水线 · openai-agents 版",
    description="进程 1 情感识别 → 进程 2 分支回应，FastAPI 作为后端编排。",
    version="1.0.0",
)

# 允许前端独立部署（如 Vite / Nginx）跨域调用
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
        "framework": "openai-agents",
        "model": settings.model,
        "base_url": settings.base_url,
        "mock": settings.mock,
        "stage1": "sentiment-analyzer",
        "stage2": ["encourager", "comforter", "responder"],
    }


@app.post("/api/chat", response_model=ChatResponse, summary="情感识别 + 分支回应")
async def chat(req: ChatRequest) -> ChatResponse:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="message 不能为空")
    try:
        return await run_pipeline(text, session_id=req.session_id)
    except Exception as exc:  # noqa: BLE001 统一包装成 502，避免把堆栈透给前端
        logger.exception("流水线执行失败")
        raise HTTPException(status_code=502, detail=f"流水线执行失败：{exc}") from exc
