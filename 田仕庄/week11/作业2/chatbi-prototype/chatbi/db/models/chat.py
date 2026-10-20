"""会话、轮次与澄清对象。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
from ..enums import ClarifyReason, ClarifyStatus, RewriteMode, TurnStatus


class ChatSession(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "chat_session"
    __table_args__ = (
        Index("ix_chat_session_user", "tenant_id", "user_id", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    turns: Mapped[list["ChatTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatTurn.turn_index"
    )
    clarifies: Mapped[list["ChatClarify"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class ChatTurn(Base, CreatedAtMixin):
    __tablename__ = "chat_turn"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_turn_index"),
        Index("ix_chat_turn_session", "session_id", "turn_index"),
    )

    turn_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_session.session_id", ondelete="CASCADE"), nullable=False
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TurnStatus] = mapped_column(enum_col(TurnStatus), nullable=False)
    rewrite_mode: Mapped[RewriteMode] = mapped_column(
        enum_col(RewriteMode), nullable=False, default=RewriteMode.new
    )
    context_note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    dsl: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    sql_text: Mapped[Optional[str]] = mapped_column(Text)
    columns: Mapped[Optional[list[str]]] = mapped_column(JSONType)
    row_sample: Mapped[Optional[list[list[Any]]]] = mapped_column(JSONType)  # ≤100 行
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    options: Mapped[Optional[list[Any]]] = mapped_column(JSONType)
    trace: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)

    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    release_id: Mapped[Optional[int]] = mapped_column(BigIntPK)

    session: Mapped["ChatSession"] = relationship(back_populates="turns")


class ChatClarify(Base, CreatedAtMixin):
    __tablename__ = "chat_clarify"
    __table_args__ = (
        Index(
            "ix_chat_clarify_pending",
            "session_id",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    clarify_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_session.session_id", ondelete="CASCADE"), nullable=False
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[ClarifyReason] = mapped_column(enum_col(ClarifyReason, 32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    partial_trace: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    options: Mapped[list[Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[ClarifyStatus] = mapped_column(
        enum_col(ClarifyStatus), nullable=False, default=ClarifyStatus.pending
    )
    resolved_choice: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["ChatSession"] = relationship(back_populates="clarifies")
