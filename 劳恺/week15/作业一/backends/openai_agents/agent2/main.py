"""OpenAI Agents SDK Agent 2 服务：情绪响应。

工作流在 ``backends.openai_agents.workflow_oa``，本文件只负责 HTTP 协议。
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI, HTTPException

from backends.openai_agents.workflow_oa import run_respond
from backends.shared.config.profiles import get_profile
from backends.shared.errors import WorkflowError
from backends.shared.llm.client import has_api_key
from backends.shared.schemas.agent_api import HealthResponse, RespondRequest
from backends.shared.schemas.response import ResponseResult

logger = logging.getLogger(__name__)

PROFILE = get_profile("openai_agents")

app = FastAPI(title="Agents SDK Agent 2 - 情绪响应", version="0.1.0")


@app.post("/respond", response_model=ResponseResult)
async def respond(request: RespondRequest) -> ResponseResult:
    """根据情绪识别结果生成回复。"""
    try:
        return await run_respond(
            request.message,
            request.emotion_result,
            request.history,
            request.retry_feedback,
        )
    except WorkflowError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if has_api_key() else "degraded",
        service="agent2",
        framework=PROFILE.key,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PROFILE.agent2_port)
