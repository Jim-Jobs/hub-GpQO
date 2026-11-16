"""文档 API 单测（不依赖真实 ES / Kafka）。

用 FakeES / FakeProducer 注入，验证的是**契约行为**：状态码、响应结构、错误码、
部分成功语义、路径穿越防护。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.documents import format_sse, resolve_image_path
from app.api.errors import ApiError
from app.main import create_app
from app.schemas.index import DocumentDoc, utcnow


class FakeES:
    """内存版 ES。"""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.deleted_content: list[str] = []

    async def ensure_indices(self) -> list[str]:
        return []

    async def index_document(self, index: str, doc_id: str, source: dict) -> None:
        self.documents[doc_id] = dict(source)

    async def get(self, index: str, doc_id: str) -> dict | None:
        return self.documents.get(doc_id)

    async def exists(self, index: str, doc_id: str) -> bool:
        return doc_id in self.documents

    async def delete(self, index: str, doc_id: str) -> bool:
        return self.documents.pop(doc_id, None) is not None

    async def delete_document(self, doc_id: str) -> bool:
        self.deleted_content.append(doc_id)
        return self.documents.pop(doc_id, None) is not None

    async def delete_document_content(self, doc_id: str) -> None:
        self.deleted_content.append(doc_id)

    async def list_documents(self, size: int = 200) -> list[dict]:
        rows = [{"doc_id": doc_id, **source} for doc_id, source in self.documents.items()]
        return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)

    async def search(self, index: str, body: dict[str, Any]) -> dict:
        return {"hits": {"total": {"value": 0}, "hits": []}}

    async def close(self) -> None:
        return None


class FakeProducer:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.statuses: list[dict] = []
        self.dlq: list[dict] = []
        self.closed = False

    async def send_upload(self, message: dict) -> None:
        self.uploads.append(message)

    async def send_status(self, event: dict) -> None:
        self.statuses.append(event)

    async def send_dlq(self, message: dict) -> None:
        self.dlq.append(message)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def client(settings, monkeypatch) -> TestClient:
    es = FakeES()
    producer = FakeProducer()
    app = create_app(settings, es_client=es, producer=producer)
    app.state.fake_es = es
    app.state.fake_producer = producer
    with TestClient(app) as test_client:
        test_client.ctx_es = es  # type: ignore[attr-defined]
        test_client.ctx_producer = producer  # type: ignore[attr-defined]
        yield test_client


def _pdf(name: str = "财报.pdf", content: bytes = b"%PDF-1.4 test") -> tuple:
    return ("files", (name, content, "application/pdf"))


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


def test_upload_pdf_returns_202_and_enqueues(client: TestClient) -> None:
    response = client.post("/api/documents", files=[_pdf()])
    assert response.status_code == 202

    body = response.json()
    assert len(body["accepted"]) == 1
    assert body["rejected"] == []

    accepted = body["accepted"][0]
    assert accepted["status"] == "pending"
    assert accepted["doc_id"].startswith("d_")

    # ES 里建了 pending 文档，Kafka 里投了任务
    stored = client.ctx_es.documents[accepted["doc_id"]]  # type: ignore[attr-defined]
    assert stored["status"] == "pending"
    assert stored["filename"] == "财报.pdf"
    assert len(client.ctx_producer.uploads) == 1  # type: ignore[attr-defined]


def test_upload_non_pdf_is_rejected_not_fatal(client: TestClient) -> None:
    """部分成功语义：坏文件进 rejected，好文件照常入队。"""
    response = client.post(
        "/api/documents",
        files=[("files", ("readme.txt", b"hello", "text/plain")), _pdf()],
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["accepted"]) == 1
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["reason"] == "仅支持 PDF 文件"


def test_upload_oversize_is_rejected(settings, monkeypatch) -> None:
    settings.max_upload_mb = 0  # 任何非空文件都超限
    app = create_app(settings, es_client=FakeES(), producer=FakeProducer())
    with TestClient(app) as test_client:
        response = test_client.post("/api/documents", files=[_pdf()])
    body = response.json()
    assert body["accepted"] == []
    assert "上限" in body["rejected"][0]["reason"]


def test_upload_without_kafka_returns_rejected_and_rolls_back(settings) -> None:
    """入队失败要回滚，不能留下永远 pending 的文档。"""
    es = FakeES()
    app = create_app(settings, es_client=es, producer=None)

    class FailingProducer(FakeProducer):
        async def send_upload(self, message: dict) -> None:
            raise RuntimeError("kafka down")

    app.state.producer = FailingProducer()
    with TestClient(app) as test_client:
        response = test_client.post("/api/documents", files=[_pdf()])
    body = response.json()
    assert body["accepted"] == []
    assert es.documents == {}


def test_upload_too_many_files(settings) -> None:
    settings.max_files_per_upload = 1
    app = create_app(settings, es_client=FakeES(), producer=FakeProducer())
    with TestClient(app) as test_client:
        response = test_client.post("/api/documents", files=[_pdf("a.pdf"), _pdf("b.pdf")])
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------


def test_list_documents_sorted_desc(client: TestClient) -> None:
    for index, doc_id in enumerate(["d_old", "d_new"]):
        doc = DocumentDoc(
            doc_id=doc_id,
            filename=f"{doc_id}.pdf",
            file_path="/tmp/x.pdf",
            created_at=utcnow(),
        )
        doc.created_at = doc.created_at.replace(year=2025 + index)
        client.ctx_es.documents[doc_id] = doc.to_es()  # type: ignore[attr-defined]

    response = client.get("/api/documents")
    assert response.status_code == 200
    rows = response.json()
    assert [row["doc_id"] for row in rows] == ["d_new", "d_old"]
    assert rows[0]["status"] == "pending"


def test_get_document_404_shape(client: TestClient) -> None:
    response = client.get("/api/documents/d_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert "不存在" in body["detail"]


# ---------------------------------------------------------------------------
# 删除 / 重试
# ---------------------------------------------------------------------------


def test_delete_document(client: TestClient) -> None:
    client.post("/api/documents", files=[_pdf()])
    doc_id = next(iter(client.ctx_es.documents))  # type: ignore[attr-defined]

    response = client.delete(f"/api/documents/{doc_id}")
    assert response.status_code == 200
    assert response.json() == {"doc_id": doc_id, "deleted": True}
    assert doc_id not in client.ctx_es.documents  # type: ignore[attr-defined]
    assert doc_id in client.ctx_es.deleted_content  # type: ignore[attr-defined]


def test_retry_only_for_failed(client: TestClient) -> None:
    client.post("/api/documents", files=[_pdf()])
    doc_id = next(iter(client.ctx_es.documents))  # type: ignore[attr-defined]

    response = client.post(f"/api/documents/{doc_id}/retry")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_retry_resets_and_requeues(client: TestClient) -> None:
    client.post("/api/documents", files=[_pdf()])
    doc_id = next(iter(client.ctx_es.documents))  # type: ignore[attr-defined]
    client.ctx_es.documents[doc_id].update(  # type: ignore[attr-defined]
        {"status": "failed", "error": "解析失败", "progress": 0.5, "stage": "mineru_parse"}
    )

    response = client.post(f"/api/documents/{doc_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    stored = client.ctx_es.documents[doc_id]  # type: ignore[attr-defined]
    assert stored["status"] == "pending"
    assert stored["error"] is None
    assert stored["progress"] == 0.0
    assert len(client.ctx_producer.uploads) == 2  # type: ignore[attr-defined]


def test_retry_missing_document(client: TestClient) -> None:
    response = client.post("/api/documents/d_nope/retry")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 静态图片与路径穿越
# ---------------------------------------------------------------------------


def test_resolve_image_path_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "images"
    (root / "d_abc").mkdir(parents=True)
    (root / "d_abc" / "d_abc_p1_i1.png").write_bytes(b"png")

    ok = resolve_image_path(root, "d_abc", "d_abc_p1_i1.png")
    assert ok.is_file()

    with pytest.raises(ApiError) as exc_info:
        resolve_image_path(root, "..", "etc/passwd")
    assert exc_info.value.status_code == 400


def test_resolve_image_path_blocks_absolute(tmp_path: Path) -> None:
    root = tmp_path / "images"
    root.mkdir()
    with pytest.raises(ApiError):
        resolve_image_path(root, "d_abc", "/etc/passwd")


def test_static_image_endpoint(settings, tmp_path: Path) -> None:
    image_dir = settings.images_dir / "d_abc"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "d_abc_p1_i1.png").write_bytes(b"\x89PNG")

    app = create_app(settings, es_client=FakeES(), producer=FakeProducer())
    with TestClient(app) as test_client:
        response = test_client.get("/static/images/d_abc/d_abc_p1_i1.png")
        assert response.status_code == 200
        assert "immutable" in response.headers["cache-control"]

        bad = test_client.get("/static/images/d_abc/..%2F..%2Fetc%2Fpasswd")
        assert bad.status_code in (400, 404)


# ---------------------------------------------------------------------------
# SSE 格式
# ---------------------------------------------------------------------------


def test_format_sse_frame() -> None:
    frame = format_sse("status", {"doc_id": "d_1", "status": "done"})
    assert frame.startswith("event: status\n")
    assert frame.endswith("\n\n")
    assert '{"doc_id": "d_1", "status": "done"}' in frame


def test_sse_endpoint_reports_events_route_not_doc_id(settings) -> None:
    """``/documents/events`` 不能被 ``/documents/{doc_id}`` 抢先匹配。"""
    from app.api.documents import router

    paths = [route.path for route in router.routes]
    assert paths.index("/api/documents/events") < paths.index("/api/documents/{doc_id}")
