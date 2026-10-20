"""组织、角色与数据源。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, BigIntPK, CreatedAtMixin, TenantMixin


class Tenant(Base, CreatedAtMixin):
    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    users: Mapped[list["AppUser"]] = relationship(back_populates="tenant")


class AppUser(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "app_user"

    # 覆盖 Mixin 的同名列，补上指向 tenant 的外键
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenant.tenant_id"), nullable=False, default="default", index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    email: Mapped[Optional[str]] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_role", back_populates="users", viewonly=True
    )


class Role(Base, TenantMixin):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_role_tenant_code"),)

    role_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    users: Mapped[list["AppUser"]] = relationship(
        secondary="user_role", back_populates="roles", viewonly=True
    )


class UserRole(Base):
    """复合主键关联表，租户由两端约束。"""

    __tablename__ = "user_role"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("app_user.user_id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("role.role_id", ondelete="CASCADE"), primary_key=True
    )


class DataSource(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "data_source"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_data_source_name"),)

    ds_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)   # sqlite|postgres|mysql|hive|doris
    dsn_ref: Mapped[str] = mapped_column(String(256), nullable=False)  # 只存密钥引用
    readonly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=30000)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
