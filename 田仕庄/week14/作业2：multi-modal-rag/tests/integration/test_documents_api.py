from __future__ import annotations


def _upload(client, kb: str = "kb-doc") -> dict:
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": kb},
        files=[("files", ("doc.pdf", b"%PDF-1.4 doc", "application/pdf"))],
    )
    assert response.status_code == 200
    return response.json()["items"][0]


def test_upload_then_list_and_status(client):
    item = _upload(client)
    file_id = item["file_id"]

    listing = client.get("/documents", params={"knowledge_base_id": "kb-doc"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["file_id"] == file_id
    assert body["items"][0]["status"] == "PENDING"

    status = client.get("/documents/status", params={"file_ids": str(file_id)})
    assert status.status_code == 200
    assert status.json()["items"][0]["status"] == "PENDING"


def test_reparse_failed_file(client):
    from app.ingest import state

    item = _upload(client, kb="kb-reparse")
    file_id = item["file_id"]

    assert state.mark_failed(file_id, code="PARSE_FAILED", message="boom")

    response = client.post(f"/documents/{file_id}/reparse")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["message"]

    again = client.post(f"/documents/{file_id}/reparse")
    assert again.status_code == 409


def test_delete_document_removes_row(client):
    from app.db import Session
    from app.models import File

    item = _upload(client, kb="kb-del")
    file_id = item["file_id"]

    response = client.delete(f"/documents/{file_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    with Session() as session:
        assert session.get(File, file_id) is None

    listing = client.get("/documents", params={"knowledge_base_id": "kb-del"})
    assert listing.json()["total"] == 0


def test_legacy_endpoints_still_work(client):
    item = _upload(client, kb="kb-legacy")

    listing = client.get("/upload/documents", params={"knowledge_base_id": "kb-legacy"})
    assert listing.status_code == 200
    documents = listing.json()["documents"]
    assert documents[0]["file_id"] == item["file_id"]
    assert documents[0]["filestate"] == "待解析"

    deleted = client.delete(f"/upload/document/{item['file_id']}")
    assert deleted.json()["status"] == "deleted"
