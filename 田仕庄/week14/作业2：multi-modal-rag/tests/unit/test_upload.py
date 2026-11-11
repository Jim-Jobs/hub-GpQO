from __future__ import annotations

from app.config import get_settings
from app.ingest.queue import mock_sent


def _pdf(name: str, content: bytes = b"%PDF-1.4 test") -> tuple:
    return ("files", (name, content, "application/pdf"))


def test_batch_upload_partial_success(client):
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb1"},
        files=[
            _pdf("a.pdf"),
            _pdf("b.pdf", b"%PDF-1.4 second"),
            ("files", ("notes.txt", b"hello", "text/plain")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["duplicates"] == 0

    items = {item["filename"]: item for item in body["items"]}
    assert items["a.pdf"]["status"] == "PENDING"
    assert items["a.pdf"]["status_text"] == "待解析"
    assert items["notes.txt"]["status"] == "REJECTED"
    assert items["notes.txt"]["error_code"] == "UNSUPPORTED_TYPE"

    assert len(mock_sent()) == 2
    assert all(payload["trace_id"] for payload in mock_sent())


def test_duplicate_upload_does_not_requeue(client):
    first = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-dup"},
        files=[_pdf("a.pdf", b"%PDF-1.4 same")],
    )
    assert first.json()["accepted"] == 1

    second = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-dup"},
        files=[_pdf("copy.pdf", b"%PDF-1.4 same")],
    )
    body = second.json()
    assert body["duplicates"] == 1
    assert body["accepted"] == 0
    assert body["items"][0]["status"] == "DUPLICATE"
    assert body["items"][0]["existing_file_id"] == first.json()["items"][0]["file_id"]
    assert len(mock_sent()) == 1


def test_invalid_pdf_magic_is_rejected(client):
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-bad"},
        files=[_pdf("bad.pdf", b"not a pdf")],
    )

    body = response.json()
    assert body["rejected"] == 1
    assert body["items"][0]["error_code"] == "INVALID_PDF"
    assert mock_sent() == []


def test_wrong_content_type_is_rejected(client):
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-mime"},
        files=[("files", ("a.pdf", b"%PDF-1.4 ok", "text/plain"))],
    )

    body = response.json()
    assert body["rejected"] == 1
    assert body["items"][0]["error_code"] == "UNSUPPORTED_TYPE"
    assert mock_sent() == []


def test_oversize_file_is_rejected(client):
    settings = get_settings()
    original = settings.max_upload_bytes
    settings.max_upload_bytes = 10
    try:
        response = client.post(
            "/upload/documents",
            data={"knowledge_base_id": "kb-big"},
            files=[_pdf("big.pdf", b"%PDF-1.4 " + b"x" * 100)],
        )
    finally:
        settings.max_upload_bytes = original

    body = response.json()
    assert body["rejected"] == 1
    assert body["items"][0]["error_code"] == "FILE_TOO_LARGE"


def test_enqueue_failure_marks_file_failed(client, monkeypatch):
    import app.ingest.service as service
    from app.db import Session
    from app.models import File

    def boom(_payload):
        raise RuntimeError("kafka down")

    monkeypatch.setattr(service, "enqueue_parse_task", boom)

    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-enqueue"},
        files=[_pdf("a.pdf", b"%PDF-1.4 enqueue")],
    )

    item = response.json()["items"][0]
    assert item["status"] == "FAILED"
    assert item["error_code"] == "ENQUEUE_FAILED"

    with Session() as session:
        rows = session.query(File).all()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert rows[0].kafka_sent is False
