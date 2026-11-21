"""OpenAI Agents SDK Agent 1 服务：情绪识别。

工作流在 ``backends.openai_agents.workflow_oa``，本文件只负责 HTTP 协议。
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI, HTTPException

from backends.openai_agents.workflow_oa import run_analyze
from backends.shared.config.profiles import get_profile
from backends.shared.errors import WorkflowError
from backends.shared.llm.client import has_api_key
from backends.shared.schemas.agent_api import AnalyzeRequest, HealthResponse
from backends.shared.schemas.emotion import EmotionResult

logger = logging.getLogger(__name__)

PROFILE = get_profile("openai_agents")

app = FastAPI(title="Agents SDK Agent 1 - 情绪识别", version="0.1.0")


@app.post("/analyze", response_model=EmotionResult)
async def analyze(request: AnalyzeRequest) -> EmotionResult:
    """识别消息中的情绪。"""
    try:
        return await run_analyze(request.message)
    except WorkflowError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if has_api_key() else "degraded",
        service="agent1",
        framework=PROFILE.key,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PROFILE.agent1_port)
