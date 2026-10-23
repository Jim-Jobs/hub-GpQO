"""受控词表（向量表见 embedding_pg.py，按需启用）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, BigIntPK, CreatedAtMixin, TenantMixin


class SemanticTerm(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "semantic_term"
    __table_args__ = (
        UniqueConstraint("tenant_id", "term", "target_type", "target_name", name="uq_term_target"),
        Index(
            "ix_semantic_term_lookup", "tenant_id", "term",
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    term_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # metric|dimension|dimension_value
    target_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_value: Mapped[Optional[str]] = mapped_column(String(256))
    weight: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
