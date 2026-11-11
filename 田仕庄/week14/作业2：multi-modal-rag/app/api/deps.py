"""API 依赖：知识库 API Key 校验。"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException

from app.db import Session
from app.models import KbApiKey


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _auth_enforced() -> bool:
    with Session() as session:
        return session.query(KbApiKey).count() > 0


def verify_kb_access(
    api_key: str | None,
    knowledge_base_id: str,
    required_role: str = "read",
) -> None:
    """没有任何 Key 时放行（单机 Demo）；注册过 Key 后强制校验。"""
    if not _auth_enforced():
        return

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    with Session() as session:
        row = (
            session.query(KbApiKey)
            .filter(KbApiKey.api_key_hash == hash_api_key(api_key))
            .first()
        )

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if row.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=403, detail="No access to this knowledge base")
    if required_role == "write" and row.role not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")
