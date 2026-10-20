"""语义层元数据：发布批次 + 版本化对象 + 审计事件。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
from ..enums import ApprovalAction, ObjStatus, ObjType, ReleaseStatus


class SemanticRelease(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "semantic_release"
    __table_args__ = (UniqueConstraint("tenant_id", "label", name="uq_release_label"),)

    release_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ReleaseStatus] = mapped_column(
        enum_col(ReleaseStatus), nullable=False, default=ReleaseStatus.draft
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    objects: Mapped[list["SemanticObject"]] = relationship(back_populates="release")


class SemanticObject(Base, TenantMixin, CreatedAtMixin):
    """统一版本化对象表：obj_type + name + version 唯一，payload 不可变。"""

    __tablename__ = "semantic_object"
    __table_args__ = (
        UniqueConstraint("tenant_id", "obj_type", "name", "version", name="uq_semantic_version"),
        # 同一对象只允许一个线上版本
        Index(
            "uq_semantic_published",
            "tenant_id", "obj_type", "name",
            unique=True,
            postgresql_where=text("status = 'published'"),
            sqlite_where=text("status = 'published'"),
        ),
        Index(
            "ix_semantic_object_pending",
            "tenant_id", "status",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_semantic_object_lookup", "tenant_id", "obj_type", "name", "version"),
    )

    object_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    obj_type: Mapped[ObjType] = mapped_column(enum_col(ObjType), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ObjStatus] = mapped_column(enum_col(ObjStatus), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    release_id: Mapped[Optional[int]] = mapped_column(
        BigIntPK, ForeignKey("semantic_release.release_id")
    )
    prev_version: Mapped[Optional[int]] = mapped_column(Integer)

    release: Mapped[Optional["SemanticRelease"]] = relationship(back_populates="objects")


class SemanticEvent(Base, TenantMixin, CreatedAtMixin):
    """审批/变更事件链：只追加，不更新、不删除。"""

    __tablename__ = "semantic_event"
    __table_args__ = (
        Index("ix_semantic_event_obj", "tenant_id", "obj_type", "name", "version", "event_id"),
    )

    event_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    obj_type: Mapped[ObjType] = mapped_column(enum_col(ObjType), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[ApprovalAction] = mapped_column(enum_col(ApprovalAction), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    request_id: Mapped[Optional[str]] = mapped_column(String(64))
