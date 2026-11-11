from __future__ import annotations


def _create(client, key: str = "secret-key", kb: str = "kb-key"):
    return client.post(
        "/kb/api-keys",
        json={"knowledge_base_id": kb, "api_key": key, "role": "write"},
    )


def test_bootstrap_key_then_auth_is_enforced(client):
    first = _create(client)
    assert first.status_code == 200
    assert first.json()["status"] == "created"

    without_key = client.get("/documents", params={"knowledge_base_id": "kb-key"})
    assert without_key.status_code == 401

    with_key = client.get(
        "/documents",
        params={"knowledge_base_id": "kb-key"},
        headers={"X-API-Key": "secret-key"},
    )
    assert with_key.status_code == 200


def test_duplicate_key_is_rejected(client):
    assert _create(client).status_code == 200

    duplicate = client.post(
        "/kb/api-keys",
        json={"knowledge_base_id": "kb-key", "api_key": "secret-key"},
        headers={"X-API-Key": "secret-key"},
    )
    assert duplicate.status_code == 409


def test_key_requires_minimum_length(client):
    response = client.post(
        "/kb/api-keys",
        json={"knowledge_base_id": "kb-key", "api_key": "short"},
    )
    assert response.status_code == 422
