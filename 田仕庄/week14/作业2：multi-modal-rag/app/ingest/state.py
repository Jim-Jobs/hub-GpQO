"""文件解析状态机：所有状态变更的唯一入口。

PENDING --落盘/入队--> PARSING --flush 成功--> DONE
   |                       |
   +--投递失败/排队超时------+--> FAILED
DONE/FAILED --重新解析--> PENDING
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.db import Session, utcnow
from app.models import File

PENDING = "PENDING"
PARSING = "PARSING"
DONE = "DONE"
FAILED = "FAILED"

ALL_STATUSES = (PENDING, PARSING, DONE, FAILED)

STATUS_TEXT = {
    PENDING: "待解析",
    PARSING: "解析中",
    DONE: "解析完成",
    FAILED: "解析失败",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    PENDING: {PARSING, FAILED},
    PARSING: {DONE, FAILED, PENDING},
    DONE: {PENDING},
    FAILED: {PENDING},
}

TERMINAL_STATUSES = (DONE, FAILED)


class TransitionError(ValueError):
    """非法状态迁移（代码 bug，而非业务失败）。"""


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status)


def transition(
    file_id: int,
    to_status: str,
    *,
    expect: str | None = None,
    **fields: Any,
) -> bool:
    """推进状态；返回 False 表示文件不存在或当前状态与 expect 不符。"""
    if to_status not in ALL_STATUSES:
        raise TransitionError(f"unknown status: {to_status}")

    with Session() as session:
        row = session.get(File, file_id)
        if row is None:
            return False
        current = row.status
        if expect is not None and current != expect:
            return False
        if to_status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise TransitionError(f"illegal transition {current} -> {to_status}")

        values: dict[str, Any] = {
            key: value for key, value in fields.items() if hasattr(File, key)
        }
        values["status"] = to_status
        values["updated_at"] = utcnow()

        result = session.execute(
            update(File)
            .where(File.id == file_id, File.status == current)
            .values(**values)
        )
        session.commit()
        return result.rowcount == 1


def mark_parsing(file_id: int, *, attempt: int) -> bool:
    return transition(
        file_id,
        PARSING,
        expect=PENDING,
        attempts=attempt,
        started_at=utcnow(),
        finished_at=None,
        error_code=None,
        error_message=None,
    )


def mark_done(
    file_id: int,
    *,
    page_count: int | None = None,
    chunk_count: int | None = None,
) -> bool:
    return transition(
        file_id,
        DONE,
        expect=PARSING,
        page_count=page_count,
        chunk_count=chunk_count,
        finished_at=utcnow(),
        error_code=None,
        error_message=None,
    )


def mark_failed(file_id: int, *, code: str, message: str) -> bool:
    with Session() as session:
        row = session.get(File, file_id)
        if row is None or row.status not in (PENDING, PARSING):
            return False
        current = row.status
    return transition(
        file_id,
        FAILED,
        expect=current,
        error_code=code,
        error_message=message[:1000],
        finished_at=utcnow(),
    )


def reset_for_reparse(file_id: int) -> bool:
    """FAILED / DONE -> PENDING；由调用方负责重新入队。"""
    with Session() as session:
        row = session.get(File, file_id)
        if row is None or row.status not in (DONE, FAILED):
            return False
        current = row.status
    return transition(
        file_id,
        PENDING,
        expect=current,
        kafka_sent=False,
        requeue_count=0,
        error_code=None,
        error_message=None,
        started_at=None,
        finished_at=None,
    )
