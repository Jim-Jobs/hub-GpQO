"""查询审计：一次问数一行，含权限快照与语义层快照号。"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import CHAR, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin


class QueryAudit(Base, TenantMixin, CreatedAtMixin):
    """PG 上以 created_at 按月分区；ORM 映射父表，SQLite 建普通表。"""

    __tablename__ = "query_audit"
    __table_args__ = (
        Index("ix_query_audit_user", "tenant_id", "user_id", "created_at"),
    )

    audit_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(64))
    turn_index: Mapped[Optional[int]] = mapped_column(Integer)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    dsl: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    sql_text: Mapped[Optional[str]] = mapped_column(Text)
    sql_hash: Mapped[Optional[str]] = mapped_column(CHAR(64))
    release_id: Mapped[Optional[int]] = mapped_column(BigIntPK)
    perm_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)

    row_count: Mapped[Optional[int]] = mapped_column(Integer)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
