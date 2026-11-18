import pytest


def test_index_serves_frontend_shell(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/styles.css" in response.text
    assert "/app.js" in response.text


def test_static_assets_are_served(client):
    script = client.get("/app.js")
    style = client.get("/styles.css")

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "conversation_id" in script.text

    assert style.status_code == 200
    assert "text/css" in style.headers["content-type"]
    assert ".message" in style.text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
