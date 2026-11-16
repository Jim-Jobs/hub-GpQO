"""文档上传和状态查询 HTTP 路由测试。"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.api.document_status import create_document_status_router
from backend.app.api.document_upload import create_document_upload_router
from backend.app.core.config import Settings
from backend.app.documents.application.upload_documents import UploadDependencies
from backend.app.documents.domain.models import DocumentRecord, DocumentStatus
from backend.tests.unit.support import MemoryDocumentStore, MemoryFile, MemoryIngestProducer, run_immediate


def _test_routers(tmp_path: Path):
    """组装使用内存依赖和临时数据目录的文档路由。"""
    settings = Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)})
    store = MemoryDocumentStore()
    producer = MemoryIngestProducer()
    upload_router = create_document_upload_router(UploadDependencies(settings, store, producer))
    status_router = create_document_status_router(store)
    return upload_router, status_router, store, producer


def _route_handler(router, path: str):
    """从路由集合中取出指定路径的处理函数，直接验证协议转换逻辑。"""
    return next(route.endpoint for route in router.routes if route.path == path)


def test_upload_endpoint_returns_each_file_status_and_continues_after_invalid_pdf(tmp_path: Path) -> None:
    """multipart 批量上传应逐文件返回结果，坏 PDF 不阻断有效文件。"""
    upload_router, _, store, producer = _test_routers(tmp_path)
    endpoint = _route_handler(upload_router, "/api/documents")

    response = run_immediate(
        endpoint(
            [
                MemoryFile("bad.pdf", b"not pdf"),
                MemoryFile("good.pdf", b"%PDF-1.7 body"),
            ]
        )
    )

    assert [result.status for result in response] == [DocumentStatus.FAILED, DocumentStatus.PENDING]
    assert len(store.records) == 2
    assert len(producer.events) == 1


def test_document_status_endpoint_returns_record_and_404_for_unknown_id(tmp_path: Path) -> None:
    """状态路由返回已保存记录，并对未知文档 ID 返回 404。"""
    _, status_router, store, _ = _test_routers(tmp_path)
    record = DocumentRecord("doc-1", "report.pdf", DocumentStatus.PENDING)
    store.save(record)
    endpoint = _route_handler(status_router, "/api/documents/{document_id}")

    response = endpoint("doc-1")
    with pytest.raises(HTTPException) as missing_response:
        endpoint("unknown")

    assert response.document_id == "doc-1"
    assert missing_response.value.status_code == 404
