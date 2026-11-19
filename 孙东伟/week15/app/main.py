"""后端进程：FastAPI 服务，接收前端输入并串联进程 1 / 进程 2。

对外只暴露一套接口，通过 ``engine`` 参数切换 LangGraph 或 OpenAI Agents SDK，
这样作业 1 的「两套实现」可以在同一个服务里直接对比。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.engines.registry import build_engines
from app.prompts import CRISIS_KEYWORDS  # noqa: F401  (文档展示用)
from app.schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("week15.api")

ENGINES = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    ENGINES.update(build_engines())
    logger.info("引擎就绪: %s | mode=%s | model=%s", list(ENGINES), settings.mode, settings.model)
    yield
    ENGINES.clear()


app = FastAPI(
    title="情绪陪伴助手 · 双引擎版",
    description="作业 1：LangGraph / OpenAI Agents SDK 两套情感识别 + 回应流水线；作业 2：Docker 化部署。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": settings.mode,
        "model": settings.model,
        "default_engine": settings.default_engine,
        "engines": sorted(ENGINES),
    }


@app.get("/api/engines")
async def engines() -> dict:
    return {"engines": sorted(ENGINES), "default": settings.default_engine}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    name = (req.engine or settings.default_engine).lower()
    engine = ENGINES.get(name)
    if engine is None:
        raise HTTPException(status_code=400, detail=f"未知引擎 {name!r}，可选：{sorted(ENGINES)}")

    trace_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    result = await engine.run(req.text.strip())
    latency_ms = int((time.perf_counter() - started) * 1000)

    logger.info(
        "trace=%s engine=%s mode=%s label=%s crisis=%s latency=%dms",
        trace_id,
        name,
        result.mode,
        result.sentiment.label.value,
        result.crisis,
        latency_ms,
    )
    return ChatResponse(
        reply=result.reply,
        sentiment=result.sentiment,
        engine=name,
        mode=result.mode,  # type: ignore[arg-type]
        crisis=result.crisis,
        trail=result.trail,
        latency_ms=latency_ms,
        trace_id=trace_id,
    )


@app.get("/")
async def root() -> dict:
    return {
        "name": "sentiment-companion",
        "docs": "/docs",
        "health": "/api/health",
        "chat": "POST /api/chat",
        "frontend": "streamlit run frontend/streamlit_app.py",
    }
