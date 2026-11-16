"""文档管理路由：上传 / 列表 / 详情 / 删除 / 重试 / 状态 SSE / 静态图片。

对应文档：``docs/04-API契约.md`` §2、§3（**前端已按此开发，勿改契约**）。

关键设计：

* **上传立即返回 202** —— 不等解析。前端拿到 ``doc_id`` 立刻插入列表，后续进度靠 SSE。
* **部分成功语义** —— 批量上传里单个文件失败不影响其他文件。
* **SSE 只是加速器** —— 状态真相在 ES；前端断线后 ``GET /api/documents`` 能拉回全量。
* **静态图片必须做路径穿越校验** —— 这是唯一直接读文件系统的入口。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import WithJsonSchema

from app.api.errors import (
    bad_request,
    conflict,
    not_found,
    payload_too_large,
    service_unavailable,
)
from app.config import Settings
from app.schemas.documents import (
    AcceptedFile,
    DeleteResponse,
    DocumentResponse,
    RejectedFile,
    RetryResponse,
    UploadResponse,
)
from app.schemas.index import DocumentDoc, new_doc_id, new_task_id, utcnow
from app.storage.es_client import ESClient, ESError
from app.storage.index_schema import INDEX_DOCUMENTS

logger = logging.getLogger(__name__)

#: 上传文件的类型别名 —— 显式声明 JSON Schema，Swagger UI 才会渲染成文件选择框
UploadFileBinary = Annotated[
    UploadFile,
    WithJsonSchema({"type": "string", "format": "binary"}),
]

router = APIRouter(prefix="/api", tags=["documents"])
#: 静态图片按契约挂在 ``/static``（**不带** ``/api`` 前缀），见 docs/04 §2.6
static_router = APIRouter(tags=["static"])

#: SSE 心跳间隔（docs/04 §3：每 25 秒一个 ``: ping``）
PING_INTERVAL_SECONDS = 25.0
#: 单次读取上传文件的分块大小
UPLOAD_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# 依赖获取
# ---------------------------------------------------------------------------


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _es(request: Request) -> ESClient:
    return request.app.state.es_client


def _producer(request: Request) -> Any:
    producer = getattr(request.app.state, "producer", None)
    if producer is None:
        raise service_unavailable("Kafka 不可用，请稍后重试")
    return producer


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


async def _save_upload(upload: UploadFile, target: Path, max_bytes: int) -> int:
    """流式落盘并计算大小；超过上限立即中止并删除半成品。"""
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(UPLOAD_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise payload_too_large(
                        f"文件超过上限 {max_bytes // (1024 * 1024)}MB"
                    )
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return size


def _is_pdf(upload: UploadFile) -> bool:
    name = (upload.filename or "").lower()
    return name.endswith(".pdf")


@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_documents(
    request: Request, files: Annotated[list[UploadFileBinary], File()]
) -> UploadResponse:
    """批量上传 PDF。**立即返回**，不等解析。"""
    settings = _settings(request)
    es = _es(request)

    if not files:
        raise bad_request("请至少上传一个文件")
    if len(files) > settings.max_files_per_upload:
        raise bad_request(f"单次最多上传 {settings.max_files_per_upload} 个文件")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    accepted: list[AcceptedFile] = []
    rejected: list[RejectedFile] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        if not _is_pdf(upload):
            rejected.append(RejectedFile(filename=filename, reason="仅支持 PDF 文件"))
            continue

        doc_id = new_doc_id()
        target = settings.uploads_dir / f"{doc_id}.pdf"
        try:
            size = await _save_upload(upload, target, max_bytes)
        except Exception as exc:  # noqa: BLE001 —— 单个文件失败不拖垮整批
            rejected.append(RejectedFile(filename=filename, reason=str(exc)))
            continue

        content_hash = await asyncio.to_thread(_sha256, target)
        doc = DocumentDoc(
            doc_id=doc_id,
            filename=filename,
            file_path=str(target),
            content_hash=content_hash,
            size_bytes=size,
            status="pending",
            progress=0.0,
            stage="queued",
        )

        try:
            await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())
        except ESError as exc:
            target.unlink(missing_ok=True)
            rejected.append(RejectedFile(filename=filename, reason=f"写入数据库失败: {exc}"))
            continue

        try:
            producer = _producer(request)
            await producer.send_upload(
                {
                    "task_id": new_task_id(),
                    "doc_id": doc_id,
                    "filename": filename,
                    "content_hash": content_hash,
                    "file_path": str(target),
                    "uploaded_at": utcnow().isoformat(),
                    "attempt": 0,
                }
            )
        except Exception as exc:  # noqa: BLE001 —— 入队失败要回滚，不能留下永远 pending 的文档
            logger.error("投递 Kafka 失败 doc=%s: %s", doc_id, exc)
            await es.delete(INDEX_DOCUMENTS, doc_id)
            target.unlink(missing_ok=True)
            rejected.append(
                RejectedFile(filename=filename, reason="队列不可用，请稍后重试")
            )
            continue

        accepted.append(AcceptedFile(doc_id=doc_id, filename=filename))

    return UploadResponse(accepted=accepted, rejected=rejected)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(UPLOAD_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 状态 SSE —— 必须定义在 /documents/{doc_id} 之前！
# FastAPI 按注册顺序匹配，否则 "events" 会被当成 doc_id 吃掉。
# ---------------------------------------------------------------------------


@router.get("/documents/events")
async def document_events(request: Request) -> StreamingResponse:
    """SSE 状态流。"""
    return StreamingResponse(
        status_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 反向代理（nginx）不缓冲，否则流式会被攒成一次性输出
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(request: Request) -> list[DocumentResponse]:
    """文档列表，按 ``created_at`` 倒序。**这是 SSE 断线后的真相来源。**"""
    rows = await _es(request).list_documents()
    return [DocumentResponse.model_validate(row) for row in rows]


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(request: Request, doc_id: str) -> DocumentResponse:
    stored = await _es(request).get(INDEX_DOCUMENTS, doc_id)
    if stored is None:
        raise not_found(f"文档不存在: {doc_id}")
    return DocumentResponse.model_validate({**stored, "doc_id": doc_id})


# ---------------------------------------------------------------------------
# 删除 / 重试
# ---------------------------------------------------------------------------


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(request: Request, doc_id: str) -> DeleteResponse:
    """删除文档及其块、图片、磁盘文件。"""
    settings = _settings(request)
    es = _es(request)
    stored = await es.get(INDEX_DOCUMENTS, doc_id)
    if stored is None:
        raise not_found(f"文档不存在: {doc_id}")

    await es.delete_document(doc_id)

    # 清理磁盘：原始 PDF 与图片目录
    pdf_path = Path(stored.get("file_path") or "")
    if pdf_path.is_file():
        pdf_path.unlink(missing_ok=True)
    from app.pipeline.image_store import delete_doc_images

    delete_doc_images(doc_id, settings.images_dir)

    logger.info("已删除文档 %s", doc_id)
    return DeleteResponse(doc_id=doc_id, deleted=True)


@router.post("/documents/{doc_id}/retry", response_model=RetryResponse)
async def retry_document(request: Request, doc_id: str) -> RetryResponse:
    """重试解析。仅对 ``status=failed`` 的文档有效。"""
    es = _es(request)
    stored = await es.get(INDEX_DOCUMENTS, doc_id)
    if stored is None:
        raise not_found(f"文档不存在: {doc_id}")
    if stored.get("status") != "failed":
        raise conflict("只有解析失败的文档可以重试")

    doc = DocumentDoc.model_validate({**stored, "doc_id": doc_id})
    doc.status = "pending"
    doc.progress = 0.0
    doc.stage = "queued"
    doc.error = None
    doc.finished_at = None
    doc.updated_at = utcnow()
    await es.index_document(INDEX_DOCUMENTS, doc_id, doc.to_es())

    await _producer(request).send_upload(
        {
            "task_id": new_task_id(),
            "doc_id": doc_id,
            "filename": doc.filename,
            "content_hash": doc.content_hash,
            "file_path": doc.file_path,
            "uploaded_at": utcnow().isoformat(),
            "attempt": 0,
        }
    )
    return RetryResponse(doc_id=doc_id, status="pending", message="已重新入队")


# ---------------------------------------------------------------------------
# 状态 SSE
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any) -> str:
    """把事件格式化成 SSE 帧。data 是 dict 时序列化为 JSON。"""
    import json

    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def status_event_stream(request: Request) -> AsyncIterator[str]:
    """状态事件流：订阅总线 → 逐条推送；每 25 秒一个心跳。"""
    bus = request.app.state.status_bus
    queue = bus.subscribe()
    try:
        # 立刻发一个心跳，让客户端与中间代理确认连接已建立
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:  # 总线关闭
                return
            yield format_sse("status", event)
    finally:
        bus.unsubscribe(queue)


# ---------------------------------------------------------------------------
# 静态图片
# ---------------------------------------------------------------------------


def resolve_image_path(images_root: Path, doc_id: str, filename: str) -> Path:
    """解析图片路径并**校验不越出 images_root**。

    拒绝 ``..``、绝对路径、符号链接逃逸（``docs/04`` §2.6）。
    """
    root = images_root.resolve()
    candidate = (root / doc_id / filename).resolve()
    if not candidate.is_relative_to(root):
        raise bad_request("非法的图片路径")
    if not candidate.is_file():
        raise not_found(f"图片不存在: {doc_id}/{filename}")
    return candidate


@static_router.get("/static/images/{doc_id}/{filename}")
async def get_image(request: Request, doc_id: str, filename: str) -> FileResponse:
    """图片静态访问。图片内容不可变，可长期缓存。"""
    path = resolve_image_path(_settings(request).images_dir, doc_id, filename)
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )



@router.post("/test-upload")
async def test_upload(
    files: list[UploadFileBinary]
):
    return {
        "filenames": [file.filename for file in files]
}