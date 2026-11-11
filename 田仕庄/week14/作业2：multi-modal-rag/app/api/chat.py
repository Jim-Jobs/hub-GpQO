"""问答接口：同时支持 JSON 文本与 multipart 图文。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.deps import verify_kb_access
from app.chat.service import ask
from app.config import get_settings
from app.errors import QUESTION_EMPTY, TOO_MANY_IMAGES, AppError
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


def _parse_top_k(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="top_k 必须是整数") from exc


def _parse_bool(raw, default: bool = True) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no")


_CHAT_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "maxLength": 4000},
                        "knowledge_base_id": {
                            "type": "string",
                            "default": "default",
                        },
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                        },
                        "use_clip": {"type": "boolean", "default": True},
                    },
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "maxLength": 4000},
                        "knowledge_base_id": {
                            "type": "string",
                            "default": "default",
                        },
                        "top_k": {"type": "integer"},
                        "use_clip": {"type": "boolean"},
                        "images": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                        },
                    },
                }
            },
        },
    }
}


@router.post(
    "/chat",
    response_model=ChatResponse,
    openapi_extra=_CHAT_OPENAPI,
)
async def chat_endpoint(
    request: Request,
    x_api_key: str | None = Header(default=None),
):
    settings = get_settings()
    content_type = request.headers.get("content-type", "")
    images: list[tuple[str, bytes]] = []

    if content_type.startswith("application/json"):
        body = await request.json()
        try:
            chat_request = ChatRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    else:
        form = await request.form()
        question = str(form.get("question") or "")
        knowledge_base_id = str(form.get("knowledge_base_id") or "default")
        top_k = _parse_top_k(form.get("top_k"))
        use_clip = _parse_bool(form.get("use_clip"), default=True)

        uploads = [
            item for item in form.getlist("images") if isinstance(item, UploadFile)
        ]
        if len(uploads) > settings.max_question_images:
            raise AppError(
                TOO_MANY_IMAGES,
                f"最多上传 {settings.max_question_images} 张图片",
                400,
            )
        for upload in uploads:
            images.append(
                (upload.filename or "image", await upload.read())
            )

        try:
            chat_request = ChatRequest(
                question=question,
                knowledge_base_id=knowledge_base_id,
                top_k=top_k,
                use_clip=use_clip,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    verify_kb_access(
        x_api_key, chat_request.knowledge_base_id, required_role="read"
    )

    if not chat_request.question.strip() and not images:
        raise AppError(QUESTION_EMPTY, status_code=400)

    return await run_in_threadpool(ask, chat_request, images)
