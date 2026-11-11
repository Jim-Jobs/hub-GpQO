from __future__ import annotations


def test_frontend_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "多模态 RAG" in response.text
    assert "知识库问答" in response.text
    assert "添加图片" in response.text
    assert "/js/main.js" in response.text
    assert "/css/base.css" in response.text


def test_frontend_assets_are_served(client):
    paths = [
        "/css/base.css",
        "/css/layout.css",
        "/css/components.css",
        "/js/api.js",
        "/js/ui.js",
        "/js/upload.js",
        "/js/documents.js",
        "/js/chat.js",
        "/js/main.js",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.text


def test_api_still_works_with_frontend_mount(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
