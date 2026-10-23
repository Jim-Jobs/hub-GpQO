"""权限规则：行级过滤与列级拒绝/脱敏/聚合。"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
from ..enums import RuleAction, RuleType, SubjectType


class PermissionRule(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "permission_rule"
    __table_args__ = (
        CheckConstraint(
            "(rule_type = 'row'    AND dimension IS NOT NULL AND op IS NOT NULL AND action = 'filter')"
            " OR "
            "(rule_type = 'column' AND field IS NOT NULL AND action IN ('deny','mask','aggregate'))",
            name="row_or_column",
        ),
        Index(
            "ix_permission_rule_subject",
            "tenant_id", "subject_type", "subject_id",
            postgresql_where=text("enabled"),
            sqlite_where=text("enabled"),
        ),
    )

    rule_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    subject_type: Mapped[SubjectType] = mapped_column(enum_col(SubjectType, 8), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(enum_col(RuleType, 8), nullable=False)

    dimension: Mapped[Optional[str]] = mapped_column(String(128))
    field: Mapped[Optional[str]] = mapped_column(String(128))
    op: Mapped[Optional[str]] = mapped_column(String(16))
    values: Mapped[Optional[list[Any]]] = mapped_column(JSONType)

    action: Mapped[RuleAction] = mapped_column(enum_col(RuleAction), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
