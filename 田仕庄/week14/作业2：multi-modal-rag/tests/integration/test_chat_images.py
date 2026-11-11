from __future__ import annotations

import io

from PIL import Image

from app.db import Session
from app.models import File


def _png(color: str = "red", size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _done_file(kb: str = "kb-img") -> int:
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    path = settings.uploads_dir / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 image chat")

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


def _image_files(count: int = 2) -> list[tuple]:
    colors = ["red", "blue", "green", "yellow"]
    return [
        ("images", (f"img{index}.png", _png(colors[index % 4]), "image/png"))
        for index in range(count)
    ]


def test_chat_with_two_images(client):
    _done_file()

    response = client.post(
        "/chat",
        data={"question": "对比这两张图的差异", "knowledge_base_id": "kb-img"},
        files=_image_files(2),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("[mock-vl]")
    assert len(body["used_images"]) == 2
    assert all(item["source"] == "user" for item in body["used_images"])
    assert body["sources"]


def test_chat_with_only_image(client):
    response = client.post(
        "/chat",
        data={"question": "", "knowledge_base_id": "kb-img"},
        files=_image_files(1),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("[mock-vl]")
    assert len(body["used_images"]) == 1
    assert body["sources"] == []


def test_chat_empty_question_without_image_is_rejected(client):
    response = client.post(
        "/chat",
        data={"question": "", "knowledge_base_id": "kb-img"},
        files=[],
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "QUESTION_EMPTY"


def test_chat_rejects_too_many_images(client):
    response = client.post(
        "/chat",
        data={"question": "看图", "knowledge_base_id": "kb-img"},
        files=_image_files(5),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "TOO_MANY_IMAGES"


def test_chat_rejects_invalid_image(client):
    response = client.post(
        "/chat",
        data={"question": "看图", "knowledge_base_id": "kb-img"},
        files=[("images", ("bad.png", b"not an image", "image/png"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "IMAGE_INVALID"
