"""聊天编排逻辑。

FastAPI 是协调中心：它负责加载历史、调用 Agent 1 与 Agent 2、执行质量检查、
失败后重试一次（最多），最后保存会话并通过 SSE 把执行过程推给前端。

Agent 1 与 Agent 2 之间不直接通信，全部经过这里。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi.concurrency import run_in_threadpool

from backends.shared.clients.agent_client import AgentClient
from backends.shared.config.profiles import FrameworkProfile
from backends.shared.db.session import Database
from backends.shared.orchestrator.sse import sse_event
from backends.shared.quality.checker import build_fallback_response, check_response
from backends.shared.schemas.agent_api import AnalyzeRequest, HistoryMessage, RespondRequest
from backends.shared.schemas.conversation import ChatRequest

logger = logging.getLogger(__name__)

async def stream_chat(
    *,
    profile: FrameworkProfile,
    database: Database,
    agent1: AgentClient,
    agent2: AgentClient,
    request: ChatRequest,
    conversation_id: str,
) -> AsyncIterator[str]:
    """产出完整聊天流程的 SSE 事件流。"""
    saved = False
    try:
        yield sse_event(
            "started",
            {
                "conversation_id": conversation_id,
                "message": request.message,
                "framework": profile.key,
                "framework_label": profile.label,
            },
        )

        history_rows = await run_in_threadpool(database.get_history_pairs, conversation_id)
        history = [HistoryMessage.model_validate(row) for row in history_rows]

        # --- Agent 1：情绪识别 ---
        yield sse_event("emotion_analysis", {"stage": "emotion_analysis", "status": "running"})
        emotion = await agent1.analyze(
            AnalyzeRequest(conversation_id=conversation_id, message=request.message)
        )
        yield sse_event("emotion_completed", emotion.model_dump(mode="json"))

        # --- Agent 2：情绪响应 ---
        yield sse_event(
            "response_generation", {"stage": "response_generation", "status": "running"}
        )
        response = await agent2.respond(
            RespondRequest(
                conversation_id=conversation_id,
                message=request.message,
                emotion_result=emotion,
                history=history,
            )
        )

        # --- 质量检查 ---
        report = check_response(emotion, response)
        yield sse_event(
            "quality_check",
            {"attempt": 1, **report.to_dict(), "strategy": response.strategy.value},
        )

        attempt = 1
        if not report.passed:
            feedback = report.feedback()
            yield sse_event(
                "retrying",
                {
                    "attempt": attempt + 1,
                    "feedback": feedback,
                    "issues": report.to_dict()["issues"],
                },
            )
            attempt += 1
            response = await agent2.respond(
                RespondRequest(
                    conversation_id=conversation_id,
                    message=request.message,
                    emotion_result=emotion,
                    history=history,
                    retry_feedback=feedback,
                )
            )
            report = check_response(emotion, response)
            yield sse_event(
                "quality_check",
                {"attempt": attempt, **report.to_dict(), "strategy": response.strategy.value},
            )

        used_fallback = False
        if not report.passed:
            # 重试后仍不合格：不无限重试，直接返回兜底回复。
            response = build_fallback_response(emotion)
            used_fallback = True
            logger.warning("质量检查未通过，已使用兜底回复: %s", report.to_dict())

        # --- 保存会话 ---
        await run_in_threadpool(
            database.add_message,
            conversation_id,
            "user",
            request.message,
            emotion_category=emotion.emotion.value,
            confidence=emotion.confidence,
            emotion_type=emotion.emotion_type,
        )
        await run_in_threadpool(
            database.add_message,
            conversation_id,
            "assistant",
            response.reply,
            emotion_category=emotion.emotion.value,
            confidence=emotion.confidence,
            emotion_type=emotion.emotion_type,
            response_strategy=response.strategy.value,
        )
        saved = True

        yield sse_event(
            "completed",
            {
                "conversation_id": conversation_id,
                "reply": response.reply,
                "strategy": response.strategy.value,
                "emotion": emotion.model_dump(mode="json"),
                "quality": report.to_dict(),
                "used_fallback": used_fallback,
            },
        )
    except Exception as exc:  # noqa: BLE001 - 任何失败都要以 failed 事件收尾
        logger.exception("聊天流程失败: %s", exc)
        if not saved:
            # 失败时至少保留用户消息，避免输入丢失。
            try:
                await run_in_threadpool(
                    database.add_message, conversation_id, "user", request.message
                )
            except Exception:  # pragma: no cover - 兜底中的兜底
                logger.exception("保存用户消息失败")
        yield sse_event("failed", {"error": str(exc), "conversation_id": conversation_id})
