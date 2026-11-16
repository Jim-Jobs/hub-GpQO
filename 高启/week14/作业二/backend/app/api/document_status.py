"""单文档处理状态 HTTP 路由。"""

from fastapi import APIRouter, HTTPException

from backend.app.api.schemas.documents import DocumentStatusResponse
from backend.app.documents.application.get_document_status import get_document_status
from backend.app.documents.application.ports import DocumentStore


def create_document_status_router(document_store: DocumentStore) -> APIRouter:
    """创建读取单个 document_id 状态的路由。"""
    router = APIRouter()

    @router.get("/api/documents/{document_id}", response_model=DocumentStatusResponse)
    def get_document_status_endpoint(document_id: str) -> DocumentStatusResponse:
        """查询 Redis 状态；未知文档返回 HTTP 404。"""
        record = get_document_status(document_id, document_store)
        if record is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return DocumentStatusResponse.model_validate(record)

    return router
