from __future__ import annotations

from app.db import Session
from app.models import File


def _done_file(kb: str = "kb-chat") -> int:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    path = settings.uploads_dir / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 chat")

    with Session() as session:
        row = File(
            filename="doc.pdf",
            filepath=str(path),
            status="DONE",
            knowledge_base_id=kb,
            page_count=3,
            chunk_count=3,
        )
        session.add(row)
        session.flush()
        file_id = row.id
        session.commit()
    return file_id


def test_chat_without_done_documents(client):
    response = client.post(
        "/chat",
        json={"question": "产品 A 何时下降？", "knowledge_base_id": "kb-empty"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "暂无解析完成" in body["answer"]
    assert body["sources"] == []


def test_chat_returns_answer_and_sources(client):
    file_id = _done_file()

    response = client.post(
        "/chat",
        json={"question": "产品 A 何时下降？", "knowledge_base_id": "kb-chat"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("[mock]")
    assert body["trace_id"]
    assert body["sources"]
    assert body["sources"][0]["file_id"] == file_id
    assert body["sources"][0]["file_name"] == "doc.pdf"
    assert body["sources"][0]["page"] == "3"
    assert body["timings"]["retrieval_ms"] >= 0


def test_chat_without_clip_uses_text_only(client):
    _done_file("kb-chat-clip")

    response = client.post(
        "/chat",
        json={
            "question": "产品 A 何时下降？",
            "knowledge_base_id": "kb-chat-clip",
            "use_clip": False,
        },
    )

    assert response.status_code == 200
    methods = {source["retrieval_method"] for source in response.json()["sources"]}
    assert methods == {"bge"}


def test_chat_validates_empty_question(client):
    response = client.post("/chat", json={"question": ""})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "QUESTION_EMPTY"


def test_openapi_documents_chat_request_body(client):
    spec = client.get("/openapi.json").json()
    content = spec["paths"]["/chat"]["post"]["requestBody"]["content"]

    assert "application/json" in content
    assert "multipart/form-data" in content
    assert "images" in content["multipart/form-data"]["schema"]["properties"]
