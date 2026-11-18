from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_TOKEN = "test-api-token-000000000000"


@pytest.fixture()
def auth_client(engine, migrated_db, monkeypatch, fake_llm):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("PIPELINE_ENGINE", engine)
    monkeypatch.setenv("API_TOKEN", API_TOKEN)

    with TestClient(create_app(Settings(_env_file=None))) as test_client:
        yield test_client


def test_chat_requires_api_token(auth_client):
    response = auth_client.post(
        "/chat",
        json={"conversation_id": str(uuid4()), "message": "你好"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_chat_accepts_valid_api_token(auth_client, fake_llm):
    fake_llm.outputs = ["negative"]

    response = auth_client.post(
        "/chat",
        json={"conversation_id": str(uuid4()), "message": "今天有点难过"},
        headers={"X-API-Token": API_TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["sentiment"] == "negative"


def test_history_requires_api_token(auth_client):
    conversation_id = uuid4()

    unauthorized = auth_client.get(f"/conversations/{conversation_id}/messages")
    authorized = auth_client.get(
        f"/conversations/{conversation_id}/messages",
        headers={"X-API-Token": API_TOKEN},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 404


def test_health_stays_public(auth_client):
    assert auth_client.get("/health").status_code == 200


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))