"""编排器 FastAPI 应用工厂。

LangGraph 与 OpenAI Agents SDK 两套版本共用这一个工厂，差异只体现在
``FrameworkProfile``（端口、Agent 地址、数据库路径）上，因此两边的 API 协议
必然完全一致。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from backends.shared.clients.agent_client import AgentClient
from backends.shared.config.profiles import FrameworkProfile
from backends.shared.config.settings import get_settings
from backends.shared.db.session import Database, make_title
from backends.shared.orchestrator.chat import stream_chat
from backends.shared.orchestrator.conversations import build_conversations_router
from backends.shared.schemas.agent_api import HealthResponse
from backends.shared.schemas.conversation import ChatRequest

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def create_app(profile: FrameworkProfile) -> FastAPI:
    """按框架画像创建编排器应用。"""
    settings = get_settings()
    database = Database(profile.database_path)
    agent1 = AgentClient(
        profile.agent1_url, f"{profile.label} Agent 1", settings.agent_request_timeout
    )
    agent2 = AgentClient(
        profile.agent2_url, f"{profile.label} Agent 2", settings.agent_request_timeout
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "%s 编排器启动，Agent1=%s Agent2=%s 数据库=%s",
            profile.label,
            profile.agent1_url,
            profile.agent2_url,
            profile.database_path,
        )
        try:
            yield
        finally:
            await agent1.aclose()
            await agent2.aclose()
            database.close()

    app = FastAPI(
        title=f"Emotion Orchestrator ({profile.label})",
        description="情感识别与情绪响应多 Agent 系统的协调中心",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.profile = profile
    app.state.database = database
    app.state.agent1 = agent1
    app.state.agent2 = agent2

    app.include_router(build_conversations_router())

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="orchestrator", framework=profile.key)

    @app.get("/health/agents", tags=["health"])
    async def agents_health(request: Request) -> dict:
        agent1_ok = await request.app.state.agent1.health()
        agent2_ok = await request.app.state.agent2.health()
        return {
            "status": "ok" if agent1_ok and agent2_ok else "degraded",
            "framework": profile.key,
            "agent1": {"url": profile.agent1_url, "reachable": agent1_ok},
            "agent2": {"url": profile.agent2_url, "reachable": agent2_ok},
        }

    @app.post("/api/chat/stream", tags=["chat"])
    async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
        conversation_id = body.conversation_id
        if conversation_id:
            existing = database.get_conversation(conversation_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="会话不存在")
        else:
            conversation = database.create_conversation(make_title(body.message))
            conversation_id = conversation.id

        # 从 app.state 取客户端，方便运行时替换（例如测试注入假实现）。
        generator = stream_chat(
            profile=profile,
            database=database,
            agent1=request.app.state.agent1,
            agent2=request.app.state.agent2,
            request=body,
            conversation_id=conversation_id,
        )
        return StreamingResponse(
            generator, media_type="text/event-stream", headers=SSE_HEADERS
        )

    return app
