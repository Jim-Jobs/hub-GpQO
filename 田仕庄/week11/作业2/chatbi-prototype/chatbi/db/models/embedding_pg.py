"""向量召回路（可选依赖：pip install pgvector）。

不随 ``chatbi.db.models`` 默认导入；仅在 PostgreSQL + pgvector 环境启用。
HNSW 索引需在迁移中用原生 DDL 创建：
    CREATE INDEX ix_embedding_hnsw ON semantic_embedding
      USING hnsw (embedding vector_cosine_ops);
"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, BigIntPK, TenantMixin


class SemanticEmbedding(Base, TenantMixin):
    __tablename__ = "semantic_embedding"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "obj_type", "obj_name", "obj_version", "model",
            name="uq_embedding_version",
        ),
    )

    embed_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    obj_type: Mapped[str] = mapped_column(String(16), nullable=False)
    obj_name: Mapped[str] = mapped_column(String(128), nullable=False)
    obj_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
