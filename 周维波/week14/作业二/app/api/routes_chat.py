"""问答接口：/api/chat（SSE 流式返回，支持“图问”上传）。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..rag import get_answerer, retrieve

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_QUERY_IMAGE_BYTES = 12 * 1024 * 1024


def _parse_doc_ids(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _save_query_image(image: UploadFile) -> str:
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".png"
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    if len(content) > MAX_QUERY_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="提问图片超过 12MB 限制")
    target = settings.tmp_dir / f"query_{uuid.uuid4().hex}{suffix}"
    await run_in_threadpool(target.write_bytes, content)
    return str(target)


@router.post("/chat")
async def chat(
    message: str = Form(""),
    image: UploadFile | None = File(None),
    doc_ids: str = Form(""),
    top_k: int = Form(0),
) -> StreamingResponse:
    question = (message or "").strip()
    if not question and image is None:
        raise HTTPException(status_code=400, detail="请输入问题或上传一张图片")

    query_image_path = await _save_query_image(image) if image is not None else None
    selected_docs = _parse_doc_ids(doc_ids) or None

    started = time.perf_counter()
    try:
        result = await run_in_threadpool(
            retrieve,
            question or "请描述这张图片的内容",
            selected_docs,
            query_image_path,
            top_k or None,
            None,
        )
    except Exception as exc:
        logger.exception("检索失败")
        if query_image_path:
            Path(query_image_path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"检索失败：{exc}") from exc

    retrieval_ms = int((time.perf_counter() - started) * 1000)
    answerer = get_answerer()
    answerer_info = answerer.describe()

    def generate():
        try:
            yield _sse(
                "meta",
                {
                    "sources": result.sources,
                    "images": result.image_urls,
                    "retrieval_ms": retrieval_ms,
                    "answerer": answerer_info,
                    "has_query_image": bool(query_image_path),
                },
            )
            if not result.chunks:
                yield _sse(
                    "warning",
                    {"message": "知识库里没有检索到相关资料，请先上传 PDF 并等待解析完成。"},
                )

            for delta in answerer.stream(
                question, result.context, result.image_paths, query_image_path
            ):
                yield _sse("delta", {"text": delta})

            yield _sse(
                "done",
                {
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "chunks": len(result.chunks),
                },
            )
        except Exception as exc:  # 模型调用失败要作为事件返回，而不是断开连接
            logger.exception("生成回答失败")
            yield _sse("error", {"message": f"模型调用失败：{exc}"})
        finally:
            # finally 里不能 yield（客户端断开会触发 GeneratorExit），只做清理
            if query_image_path:
                Path(query_image_path).unlink(missing_ok=True)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
