"""评测与用户反馈。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin


class EvalCase(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "eval_case"

    case_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="alice")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    setup_turns: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    expected_dsl: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    expected_sql: Mapped[Optional[str]] = mapped_column(Text)
    expected_rows: Mapped[Optional[list[Any]]] = mapped_column(JSONType)
    expect_status: Mapped[Optional[str]] = mapped_column(String(16))
    tolerance: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0.01)
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EvalRun(Base):
    __tablename__ = "eval_run"

    run_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    release_id: Mapped[Optional[int]] = mapped_column(BigIntPK)
    scope: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ex_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    operator: Mapped[Optional[str]] = mapped_column(String(64))

    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EvalResult(Base):
    __tablename__ = "eval_result"
    __table_args__ = (Index("ix_eval_result_run", "run_id", "passed"),)

    result_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("eval_run.run_id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[int] = mapped_column(
        BigIntPK, ForeignKey("eval_case.case_id"), nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_status: Mapped[Optional[str]] = mapped_column(String(16))
    actual_dsl: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    actual_sql: Mapped[Optional[str]] = mapped_column(Text)
    diff: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)

    run: Mapped["EvalRun"] = relationship(back_populates="results")


class Feedback(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "feedback"

    feedback_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(64))
    turn_index: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[int]] = mapped_column(Integer)   # 1 赞 / -1 踩
    corrected_question: Mapped[Optional[str]] = mapped_column(Text)
    corrected_sql: Mapped[Optional[str]] = mapped_column(Text)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
