"""创建图文问答提交、状态轮询和通用回答确认路由。"""

from collections.abc import Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.app.api.schemas.chat import ChatAcceptedResponse, ChatStatusResponse
from backend.app.chat.application.task_manager import (
    ChatConflictError,
    ChatNotFoundError,
    ChatQueueFullError,
    ChatRequestError,
    ChatTaskManager,
)
from backend.app.chat.domain.models import ChatRequest
from backend.app.integrations.errors import ChatStatusStoreError


def create_chat_router(
    manager_provider: Callable[[], ChatTaskManager], max_image_bytes: int
) -> APIRouter:
    """创建依赖任务管理器的问答 API，并限制单张上传图大小。"""
    router = APIRouter()

    @router.post("/api/chat", response_model=ChatAcceptedResponse, status_code=202)
    async def submit_chat_endpoint(
        question_text: str = Form(...),
        image: UploadFile | None = File(None),
    ) -> ChatAcceptedResponse:
        """接收图文输入，在有界内存队列中创建任务并立即返回 chat_id。"""
        request = await _read_request(question_text, image, max_image_bytes)
        try:
            record = manager_provider().submit(request)
        except ChatQueueFullError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except ChatRequestError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ChatStatusStoreError as error:
            raise HTTPException(status_code=503, detail="问答状态服务暂不可用") from error
        return ChatAcceptedResponse(chat_id=record.chat_id, status=record.status)

    @router.get("/api/chat/{chat_id}", response_model=ChatStatusResponse)
    def get_chat_status_endpoint(chat_id: str) -> ChatStatusResponse:
        """查询当前状态和终态响应；未知或过期任务返回 404。"""
        try:
            record = manager_provider().get_status(chat_id)
        except ChatNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ChatStatusStoreError as error:
            raise HTTPException(status_code=503, detail="问答状态服务暂不可用") from error
        return ChatStatusResponse.model_validate(record.__dict__)

    @router.post("/api/chat/{chat_id}/confirmation", response_model=ChatStatusResponse)
    async def confirm_chat_endpoint(
        chat_id: str,
        accept_general_answer: bool = Form(...),
        question_text: str | None = Form(None),
        image: UploadFile | None = File(None),
    ) -> ChatStatusResponse:
        """接收继续/取消决定；继续时前端重传原问题和可选图片。"""
        request = None
        if accept_general_answer:
            if question_text is None:
                raise HTTPException(status_code=422, detail="接受通用回答时必须重传原问题")
            request = await _read_request(question_text, image, max_image_bytes)
        elif image is not None:
            raise HTTPException(status_code=422, detail="取消任务时不需要上传图片")
        try:
            record = manager_provider().confirm(
                chat_id,
                accept_general_answer=accept_general_answer,
                request=request,
            )
        except ChatNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ChatConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ChatQueueFullError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except ChatRequestError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ChatStatusStoreError as error:
            raise HTTPException(status_code=503, detail="问答状态服务暂不可用") from error
        return ChatStatusResponse.model_validate(record.__dict__)

    return router


async def _read_request(
    question_text: str, image: UploadFile | None, max_image_bytes: int
) -> ChatRequest:
    """校验问题和图片类型/大小；只读取上限加一字节以尽早拒绝超限。"""
    if not question_text.strip():
        raise HTTPException(status_code=422, detail="问题文本不能为空")
    if image is None:
        return ChatRequest(question_text.strip())
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="只接受 image/* 类型的问答图片")
    data = await image.read(max_image_bytes + 1)
    if len(data) > max_image_bytes:
        raise HTTPException(status_code=413, detail="问答图片超过大小限制")
    if not data:
        raise HTTPException(status_code=422, detail="问答图片不能为空")
    return ChatRequest(question_text.strip(), data, image.content_type)
