"""解析产物资源服务：把 MinerU 抽出的图片以 HTTP 暴露给前端/前端渲染。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..db import session_scope
from ..models import Document
from ..rag.assets import resolve_asset

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("/{document_id}/{file_path:path}")
def get_asset(document_id: str, file_path: str) -> FileResponse:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        parsed_dir = document.parsed_dir

    path = resolve_asset(parsed_dir, file_path)
    if path is None:
        raise HTTPException(status_code=404, detail="资源不存在")

    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=3600"},
    )
