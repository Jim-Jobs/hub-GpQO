"""通过 document_id 和 asset_id 提供已完成文档的登记图片。"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.domain.models import DocumentStatus
from backend.app.documents.parsing.asset_registry import load_asset_bytes


def create_asset_router(parsed_root: Path, document_store: DocumentStore) -> APIRouter:
    """创建只读取已完成文档受控图片资源的 HTTP 路由。"""
    router = APIRouter()

    @router.get("/api/assets/{document_id}/{asset_id}", include_in_schema=False)
    def get_asset(document_id: str, asset_id: str) -> Response:
        """只返回属于指定 completed 文档且已登记的图片，其他情况统一 404。"""
        record = document_store.get(document_id)
        if record is None or record.status is not DocumentStatus.COMPLETED:
            raise HTTPException(status_code=404, detail="图片资源不存在")
        asset = load_asset_bytes(parsed_root, document_id, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="图片资源不存在")
        data, media_type = asset
        return Response(
            content=data,
            media_type=media_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return router
