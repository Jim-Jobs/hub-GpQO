"""关键读写操作：线上版本读取、发布事务、回滚、会话续期、澄清接续。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .enums import (
    ApprovalAction, ClarifyStatus, ObjStatus, ObjType, ReleaseStatus, TurnStatus,
)
from .models.chat import ChatClarify, ChatSession, ChatTurn
from .models.semantic import SemanticEvent, SemanticObject, SemanticRelease


def load_published(db: Session, tenant_id: str, obj_type: ObjType, name: str) -> Optional[SemanticObject]:
    return db.scalar(
        select(SemanticObject).where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
            SemanticObject.status == ObjStatus.published,
        )
    )


def load_all_published(db: Session, tenant_id: str) -> list[SemanticObject]:
    return list(db.scalars(
        select(SemanticObject)
        .where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.status == ObjStatus.published,
        )
        .order_by(SemanticObject.obj_type, SemanticObject.name)
    ))


def publish(db: Session, tenant_id: str, obj_type: ObjType, name: str,
            version: int, release_id: int, reviewer: str = "") -> None:
    """发布指定版本：旧版本下线 → 新版本上线 → 批次置为已发布。

    必须在同一事务内调用（``session_scope``），顺序不可颠倒，
    否则会撞 ``uq_semantic_published`` 唯一索引。
    """
    db.execute(
        update(SemanticObject)
        .where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
            SemanticObject.status == ObjStatus.published,
            SemanticObject.version != version,
        )
        .values(status=ObjStatus.deprecated)
    )
    db.execute(
        update(SemanticObject)
        .where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
            SemanticObject.version == version,
            SemanticObject.status == ObjStatus.pending,
        )
        .values(status=ObjStatus.published, release_id=release_id,
                reviewed_by=reviewer, reviewed_at=func.now())
    )
    db.execute(
        update(SemanticRelease)
        .where(SemanticRelease.release_id == release_id)
        .values(status=ReleaseStatus.published, published_at=func.now())
    )


def rollback(db: Session, tenant_id: str, obj_type: ObjType, name: str,
             to_version: int, actor: str) -> SemanticObject:
    """以历史版本 payload 新建草稿（version+1），不删除历史、不直接上线。"""
    src = db.scalar(
        select(SemanticObject).where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
            SemanticObject.version == to_version,
        )
    )
    if src is None:
        raise ValueError(f"version {to_version} not found")

    max_v = db.scalar(
        select(func.max(SemanticObject.version)).where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
        )
    ) or 0

    draft = SemanticObject(
        tenant_id=tenant_id, obj_type=obj_type, name=name,
        version=max_v + 1, status=ObjStatus.draft,
        payload=src.payload, owner=src.owner,
        created_by=actor, prev_version=src.version, release_id=None,
    )
    db.add(draft)
    db.add(SemanticEvent(
        tenant_id=tenant_id, obj_type=obj_type, name=name, version=draft.version,
        action=ApprovalAction.rollback, actor=actor,
        comment=f"rollback from v{to_version}",
    ))
    db.flush()
    return draft


def touch_session(db: Session, session_id: str, ttl_seconds: int = 1800) -> bool:
    """滑动续期；会话不存在或已过期返回 False。"""
    now = datetime.now(timezone.utc)
    res = db.execute(
        update(ChatSession)
        .where(ChatSession.session_id == session_id, ChatSession.expires_at > now)
        .values(updated_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
    )
    return res.rowcount > 0


def last_ok_turn(db: Session, session_id: str) -> Optional[ChatTurn]:
    """上下文继承基线：最近一次成功且出数的轮次。"""
    return db.scalar(
        select(ChatTurn)
        .where(
            ChatTurn.session_id == session_id,
            ChatTurn.status == TurnStatus.ok,
            ChatTurn.row_count > 0,
        )
        .order_by(ChatTurn.turn_index.desc())
        .limit(1)
    )


def resolve_clarify(db: Session, clarify_id: str, choice: dict) -> Optional[ChatClarify]:
    c = db.get(ChatClarify, clarify_id)
    if c is None or c.status != ClarifyStatus.pending:
        return None
    if c.expires_at <= datetime.now(timezone.utc):
        c.status = ClarifyStatus.expired
        return None
    c.status = ClarifyStatus.resolved
    c.resolved_choice = choice
    return c
