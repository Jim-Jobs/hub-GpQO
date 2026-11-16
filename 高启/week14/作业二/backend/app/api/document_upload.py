"""多 PDF 上传 HTTP 路由。"""

from fastapi import APIRouter, File, UploadFile

from backend.app.api.schemas.documents import DocumentStatusResponse
from backend.app.documents.application.upload_documents import (
    UploadDependencies,
    upload_documents as upload_documents_use_case,
)


def create_document_upload_router(dependencies: UploadDependencies) -> APIRouter:
    """创建带有上传用例依赖的 PDF 上传路由。"""
    router = APIRouter()

    @router.post("/api/documents", response_model=list[DocumentStatusResponse])
    async def upload_documents_endpoint(
        files: list[UploadFile] = File(..., description="一次上传一个或多个 PDF 文件"),
    ) -> list[DocumentStatusResponse]:
        """接收 multipart PDF 文件，并逐个返回初始或失败状态。"""
        records = await upload_documents_use_case(files, dependencies)
        return [DocumentStatusResponse.model_validate(record) for record in records]

    return router
