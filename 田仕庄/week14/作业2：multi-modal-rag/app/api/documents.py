"""文档接口：批量上传、列表、状态、重解析、删除，以及旧版兼容路由。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile

from app.api.deps import verify_kb_access
from app.config import get_settings
from app.errors import AppError
from app.ingest import service, state
from app.schemas import (
    DeleteDocumentResponse,
    DocumentListResponse,
    LegacyDocumentListResponse,
    ReparseResponse,
    StatusBatchResponse,
    UploadBatchResponse,
    UploadDocumentResponse,
)

router = APIRouter(tags=["documents"])


def _parse_file_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise HTTPException(status_code=400, detail=f"非法 file_id: {part}")
        ids.append(int(part))
    if not ids:
        raise HTTPException(status_code=400, detail="file_ids 不能为空")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="单次最多查询 200 个文件")
    return ids


# ---------------------------------------------------------------------------
# 批量上传
# ---------------------------------------------------------------------------
@router.post("/upload/documents", response_model=UploadBatchResponse)
def upload_documents(
    knowledge_base_id: str = Form(default="default"),
    files: list[UploadFile] = File(...),
    x_api_key: str | None = Header(default=None),
):
    settings = get_settings()
    verify_kb_access(x_api_key, knowledge_base_id, required_role="write")
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多上传 {settings.max_upload_files} 个文件",
        )
    pairs = [
        (item.filename or "unknown.pdf", item.file, item.content_type)
        for item in files
    ]
    return service.upload_batch(knowledge_base_id, pairs)


@router.post("/upload/document", response_model=UploadDocumentResponse)
def upload_document(
    knowledge_base_id: str = Form(default="default"),
    file: UploadFile = File(...),
    x_api_key: str | None = Header(default=None),
):
    """兼容旧版单文件上传。"""
    verify_kb_access(x_api_key, knowledge_base_id, required_role="write")
    result = service.upload_batch(
        knowledge_base_id,
        [(file.filename or "unknown.pdf", file.file, file.content_type)],
    )
    item = result.items[0]
    if item.file_id is None:
        raise AppError(item.error_code or "UPLOAD_FAILED", item.error_message, 400)
    return UploadDocumentResponse(
        file_id=item.file_id,
        filename=item.filename,
        knowledge_base_id=result.knowledge_base_id,
    )


# ---------------------------------------------------------------------------
# 列表与状态
# ---------------------------------------------------------------------------
@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    knowledge_base_id: str = Query(default="default"),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None),
):
    verify_kb_access(x_api_key, knowledge_base_id, required_role="read")
    if status and status not in state.ALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态: {status}")
    return service.list_documents(
        knowledge_base_id, status=status, limit=limit, offset=offset
    )


@router.get("/documents/status", response_model=StatusBatchResponse)
def documents_status(
    file_ids: str = Query(description="逗号分隔的 file_id"),
    x_api_key: str | None = Header(default=None),
):
    ids = _parse_file_ids(file_ids)
    kb = service.get_knowledge_base_of(ids[0])
    if kb is not None:
        verify_kb_access(x_api_key, kb, required_role="read")
    return service.get_statuses(ids)


# ---------------------------------------------------------------------------
# 重解析 / 删除
# ---------------------------------------------------------------------------
@router.post("/documents/{file_id}/reparse", response_model=ReparseResponse)
def reparse_document(
    file_id: int,
    x_api_key: str | None = Header(default=None),
):
    kb = service.get_knowledge_base_of(file_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    verify_kb_access(x_api_key, kb, required_role="write")
    return service.reparse_document(file_id)


@router.delete("/documents/{file_id}", response_model=DeleteDocumentResponse)
def delete_document(
    file_id: int,
    x_api_key: str | None = Header(default=None),
):
    kb = service.get_knowledge_base_of(file_id)
    if kb is None:
        return DeleteDocumentResponse(file_id=file_id, status="not_found")
    verify_kb_access(x_api_key, kb, required_role="write")
    return service.delete_document(file_id)


# ---------------------------------------------------------------------------
# 旧版兼容
# ---------------------------------------------------------------------------
@router.get("/upload/documents", response_model=LegacyDocumentListResponse)
def legacy_list_documents(
    knowledge_base_id: str = Query(default="default"),
    x_api_key: str | None = Header(default=None),
):
    verify_kb_access(x_api_key, knowledge_base_id, required_role="read")
    return service.list_legacy(knowledge_base_id)


@router.delete("/upload/document/{file_id}", response_model=DeleteDocumentResponse)
def legacy_delete_document(
    file_id: int,
    x_api_key: str | None = Header(default=None),
):
    kb = service.get_knowledge_base_of(file_id)
    if kb is None:
        return DeleteDocumentResponse(file_id=file_id, status="not_found")
    verify_kb_access(x_api_key, kb, required_role="write")
    return service.delete_document(file_id)
