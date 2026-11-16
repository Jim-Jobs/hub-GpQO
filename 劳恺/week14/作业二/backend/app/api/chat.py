"""问答路由：``POST /api/chat``（SSE）。

对应文档：``docs/04-API契约.md` §4。

**注意**：问答是 POST，浏览器原生 ``EventSource`` 只支持 GET，所以前端要用
``fetch`` + ``ReadableStream`` 手动解析 SSE 帧（docs/04 §7.2）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.errors import bad_request
from app.llm.embedding_client import build_embedding_client
from app.llm.image_utils import decode_image_data
from app.llm.vllm_client import build_llm_client
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


def _service(request: Request) -> ChatService:
    settings = request.app.state.settings
    service = getattr(request.app.state, "chat_service", None)
    if service is None:
        service = ChatService(
            settings=settings,
            es=request.app.state.es_client,
            embedder=build_embedding_client(settings),
            llm=build_llm_client(settings),
        )
        request.app.state.chat_service = service
    return service


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """提问 → 图文混排答案（SSE）。

    事件：``delta`` / ``citation`` / ``done`` / ``error``。
    """
    if not body.question or not body.question.strip():
        raise bad_request("问题不能为空")
    if len(body.images) > request.app.state.settings.max_user_images:
        raise bad_request(
            f"用户图片最多 {request.app.state.settings.max_user_images} 张"
        )
    # 图片数据要在**进流之前**校验 —— 坏数据带着去调外部 API 只会得到语焉不详的 400
    for user_image in body.images:
        try:
            decode_image_data(user_image.data, user_image.filename)
        except ValueError as exc:
            raise bad_request(str(exc)) from exc

    service = _service(request)

    async def generate() -> Any:
        async for frame in service.stream_chat(body):
            yield frame

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 不缓冲，否则流式会被攒成一次性输出
            "X-Accel-Buffering": "no",
        },
    )
