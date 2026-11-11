from __future__ import annotations


def _register_key(raw_key: str, kb: str, role: str = "write") -> None:
    from app.api.deps import hash_api_key
    from app.db import Session
    from app.models import KbApiKey

    with Session() as session:
        session.add(
            KbApiKey(
                api_key_hash=hash_api_key(raw_key),
                knowledge_base_id=kb,
                role=role,
            )
        )
        session.commit()


def test_missing_key_is_rejected_when_auth_enforced(client):
    _register_key("secret", "kb-auth")

    response = client.get("/documents", params={"knowledge_base_id": "kb-auth"})
    assert response.status_code == 401


def test_valid_key_is_accepted(client):
    _register_key("secret", "kb-auth")

    response = client.get(
        "/documents",
        params={"knowledge_base_id": "kb-auth"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 200


def test_key_for_other_kb_is_forbidden(client):
    _register_key("secret", "kb-auth")

    response = client.get(
        "/documents",
        params={"knowledge_base_id": "kb-other"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 403


def test_read_only_key_cannot_upload(client):
    _register_key("reader", "kb-read", role="read")

    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": "kb-read"},
        headers={"X-API-Key": "reader"},
        files=[("files", ("a.pdf", b"%PDF-1.4 auth", "application/pdf"))],
    )
    assert response.status_code == 403
