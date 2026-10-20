"""ORM 基础层：Base、类型变体、公共 Mixin、枚举列工厂。"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, Integer, MetaData, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# BigInt 在 SQLite 必须降级为 Integer 才能自增
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
JSONType = JSON().with_variant(JSONB, "postgresql")

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def enum_col(e: type[enum.Enum], length: int = 16) -> SAEnum:
    """枚举列：存小写 value，不创建 PG 原生枚举类型。"""
    return SAEnum(
        e,
        native_enum=False,
        length=length,
        values_callable=lambda x: [m.value for m in x],
        validate_strings=True,
    )


class TenantMixin:
    """所有业务表共有的租户列。MVP 固定 default。"""

    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", index=True
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
