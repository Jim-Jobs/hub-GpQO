"""知识库 API Key 管理。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.api.deps import _auth_enforced, hash_api_key, verify_kb_access
from app.db import Session
from app.models import KbApiKey
from app.schemas import ApiKeyCreateRequest, ApiKeyCreateResponse

router = APIRouter(prefix="/kb", tags=["kb"])


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
def create_api_key(
    request: ApiKeyCreateRequest,
    x_api_key: str | None = Header(default=None),
):
    if _auth_enforced():
        verify_kb_access(
            x_api_key, request.knowledge_base_id, required_role="write"
        )

    key_hash = hash_api_key(request.api_key)
    with Session() as session:
        existing = (
            session.query(KbApiKey)
            .filter(KbApiKey.api_key_hash == key_hash)
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="API key already exists")

        row = KbApiKey(
            api_key_hash=key_hash,
            knowledge_base_id=request.knowledge_base_id,
            role=request.role,
        )
        session.add(row)
        session.flush()
        key_id = row.id
        session.commit()

    return ApiKeyCreateResponse(
        id=key_id,
        knowledge_base_id=request.knowledge_base_id,
        role=request.role,
    )
