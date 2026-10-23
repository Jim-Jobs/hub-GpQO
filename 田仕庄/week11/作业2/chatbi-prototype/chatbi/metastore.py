"""语义层元数据仓储：ORM 实现（对外 API 与手写 SQL 版本保持一致）。

设计要点
--------
- 所有语义层对象（model / metric / dimension）存入同一张 ``semantic_object`` 表，
  以 ``payload`` JSON 承载各自异构字段，避免为每类对象建独立表。
- 每个 (obj_type, name) 按 ``version`` 递增形成不可变历史，行内状态字段描述其生命周期：
  draft -> pending -> published / rejected；被新版本取代的已发布版本标记为 deprecated。
- ``semantic_release`` 是发布批次：一次发布把若干对象同时切到线上，对外只暴露一个
  ``release_id`` 作为原子快照号（缓存失效与评测回溯的锚点）。
- ``semantic_event`` 记录审批流每一步动作，形成可追溯审计链。
- 运行时只读取 "active published"（每个对象 status=published 的行），即线上生效的语义层。

实现说明：本模块只负责「把 ORM 行转换成审批服务需要的 dict」这一层适配，
表结构与约束由 ``chatbi/db/models`` 定义（见《04-数据模型ORM设计》）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased

from .db.base import Base
from .db.enums import ApprovalAction, ObjStatus, ObjType, ReleaseStatus
from .db.engine import engine_for
from .db.models.semantic import SemanticEvent, SemanticObject, SemanticRelease


def _enum_val(v):
    return v.value if isinstance(v, Enum) else v


def _iso(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else v


def ddl(dialect: str = "sqlite") -> str:
    """输出语义层相关表的建表 DDL（由 ORM metadata 生成，便于纳入迁移脚本）。"""
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateIndex, CreateTable

    from .db import models  # noqa: F401  注册 metadata

    d = postgresql.dialect() if dialect.lower() in ("pg", "postgres", "postgresql") \
        else sqlite.dialect()
    stmts = []
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("semantic_"):
            continue
        stmts.append(str(CreateTable(table).compile(dialect=d)).strip())
        for idx in table.indexes:
            stmts.append(str(CreateIndex(idx).compile(dialect=d)).strip())
    return ";\n".join(stmts) + ";"


class MetaStore:
    """语义层元数据仓储（SQLAlchemy ORM / SQLite 原型）。"""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = engine_for(self.db_path)
        self.db = Session(self.engine, expire_on_commit=False)
        self.init_schema()

    # ---------------- schema ----------------

    def init_schema(self):
        """建表（幂等）；表定义来自 ORM metadata。"""
        from .db import models  # noqa: F401  注册 metadata

        Base.metadata.create_all(self.engine)

    def close(self):
        self.db.close()
        self.engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- 行转换 ----------------

    @staticmethod
    def _row(o: SemanticObject) -> dict:
        return {
            "id": o.object_id,
            "tenant_id": o.tenant_id,
            "obj_type": _enum_val(o.obj_type),
            "name": o.name,
            "version": o.version,
            "status": _enum_val(o.status),
            "payload": o.payload,
            "owner": o.owner,
            "created_by": o.created_by,
            "created_at": _iso(o.created_at),
            "submitted_by": o.submitted_by,
            "submitted_at": _iso(o.submitted_at),
            "reviewed_by": o.reviewed_by,
            "reviewed_at": _iso(o.reviewed_at),
            "review_comment": o.review_comment,
            "release_id": o.release_id,
            "prev_version": o.prev_version,
        }

    @staticmethod
    def _event_row(e: SemanticEvent) -> dict:
        return {
            "id": e.event_id,
            "tenant_id": e.tenant_id,
            "obj_type": _enum_val(e.obj_type),
            "name": e.name,
            "version": e.version,
            "action": _enum_val(e.action),
            "actor": e.actor,
            "comment": e.comment,
            "created_at": _iso(e.created_at),
        }

    # ---------------- 读取 ----------------

    def all_versions(self, obj_type: str | None = None, name: str | None = None) -> list[dict]:
        stmt = select(SemanticObject)
        if obj_type:
            stmt = stmt.where(SemanticObject.obj_type == ObjType(obj_type))
        if name:
            stmt = stmt.where(SemanticObject.name == name)
        stmt = stmt.order_by(SemanticObject.obj_type, SemanticObject.name, SemanticObject.version)
        return [self._row(o) for o in self.db.scalars(stmt)]

    def latest(self, obj_type: str, name: str) -> dict | None:
        o = self.db.scalar(
            select(SemanticObject)
            .where(SemanticObject.obj_type == ObjType(obj_type), SemanticObject.name == name)
            .order_by(SemanticObject.version.desc())
            .limit(1)
        )
        return self._row(o) if o else None

    def version(self, obj_type: str, name: str, ver: int) -> dict | None:
        o = self.db.scalar(
            select(SemanticObject).where(
                SemanticObject.obj_type == ObjType(obj_type),
                SemanticObject.name == name,
                SemanticObject.version == ver,
            )
        )
        return self._row(o) if o else None

    def latest_published(self, obj_type: str, name: str) -> dict | None:
        o = self.db.scalar(
            select(SemanticObject).where(
                SemanticObject.obj_type == ObjType(obj_type),
                SemanticObject.name == name,
                SemanticObject.status == ObjStatus.published,
            )
        )
        return self._row(o) if o else None

    def active_published(self) -> list[dict]:
        """线上生效定义：每个对象仅有一条 published（由唯一索引保证）。"""
        stmt = (
            select(SemanticObject)
            .where(SemanticObject.status == ObjStatus.published)
            .order_by(SemanticObject.obj_type, SemanticObject.name)
        )
        return [self._row(o) for o in self.db.scalars(stmt)]

    def working_payload(self, obj_type: str, name: str) -> dict | None:
        """编辑基线：优先未发布的草稿/待审版本，其次线上已发布版本。"""
        latest = self.latest(obj_type, name)
        if latest and latest["status"] in ("draft", "pending"):
            return latest
        return self.latest_published(obj_type, name) or latest

    def max_version(self, obj_type: str, name: str) -> int:
        v = self.db.scalar(
            select(func.max(SemanticObject.version)).where(
                SemanticObject.obj_type == ObjType(obj_type),
                SemanticObject.name == name,
            )
        )
        return int(v or 0)

    def list_objects(self, obj_type: str | None = None) -> list[dict]:
        """每个对象仅返回其最新版本，用于列表概览。"""
        peer = aliased(SemanticObject)
        newest = (
            select(func.max(peer.version))
            .where(
                peer.obj_type == SemanticObject.obj_type,
                peer.name == SemanticObject.name,
            )
            .scalar_subquery()
        )
        stmt = select(SemanticObject).where(SemanticObject.version == newest)
        if obj_type:
            stmt = stmt.where(SemanticObject.obj_type == ObjType(obj_type))
        stmt = stmt.order_by(SemanticObject.obj_type, SemanticObject.name)
        return [self._row(o) for o in self.db.scalars(stmt)]

    def events(self, obj_type: str | None = None, name: str | None = None) -> list[dict]:
        stmt = select(SemanticEvent)
        if obj_type:
            stmt = stmt.where(SemanticEvent.obj_type == ObjType(obj_type))
        if name:
            stmt = stmt.where(SemanticEvent.name == name)
        stmt = stmt.order_by(SemanticEvent.event_id)
        return [self._event_row(e) for e in self.db.scalars(stmt)]

    # ---------------- 发布批次 ----------------

    def next_release_label(self, tenant_id: str = "default") -> str:
        """批次标签：``2026-10-20.1``，同日按序号递增。"""
        prefix = datetime.now().strftime("%Y-%m-%d") + "."
        labels = self.db.scalars(
            select(SemanticRelease.label).where(
                SemanticRelease.tenant_id == tenant_id,
                SemanticRelease.label.like(prefix + "%"),
            )
        )
        seq = 0
        for label in labels:
            try:
                seq = max(seq, int(label[len(prefix):]))
            except ValueError:
                continue
        return f"{prefix}{seq + 1}"

    def create_release(self, actor: str = "", note: str = "",
                       tenant_id: str = "default") -> int:
        rel = SemanticRelease(
            tenant_id=tenant_id, label=self.next_release_label(tenant_id),
            status=ReleaseStatus.draft, note=note, created_by=actor,
        )
        self.db.add(rel)
        self.db.commit()
        return rel.release_id

    def publish_release(self, release_id: int):
        """把一个批次整体置为已发布（批量导入等场景）。"""
        self.db.execute(
            update(SemanticRelease)
            .where(SemanticRelease.release_id == release_id)
            .values(status=ReleaseStatus.published, published_at=datetime.now())
        )
        self.db.commit()

    def release(self, release_id: int) -> dict | None:
        rel = self.db.get(SemanticRelease, release_id)
        if rel is None:
            return None
        return {
            "release_id": rel.release_id,
            "tenant_id": rel.tenant_id,
            "label": rel.label,
            "status": _enum_val(rel.status),
            "note": rel.note,
            "created_by": rel.created_by,
            "created_at": _iso(rel.created_at),
            "published_at": _iso(rel.published_at),
        }

    def latest_release(self) -> dict | None:
        rel = self.db.scalar(
            select(SemanticRelease).order_by(SemanticRelease.release_id.desc()).limit(1)
        )
        return self.release(rel.release_id) if rel else None

    # ---------------- 写入 ----------------

    def insert_version(self, obj_type: str, name: str, payload: dict, status: str,
                       actor: str, comment: str = "", owner: str = "",
                       release_id: int | None = None, prev_version: int | None = None) -> int:
        ver = self.max_version(obj_type, name) + 1
        now = datetime.now()
        obj = SemanticObject(
            obj_type=ObjType(obj_type), name=name, version=ver,
            status=ObjStatus(status), payload=payload, owner=owner,
            created_by=actor, submitted_by=actor if status == "pending" else "",
            submitted_at=now if status == "pending" else None,
            release_id=release_id, prev_version=prev_version,
        )
        self.db.add(obj)
        self.db.commit()
        self.add_event(obj_type, name, ver, "create", actor, comment)
        return ver

    def publish_version(self, obj_type: str, name: str, ver: int, actor: str,
                        comment: str = "", release_note: str = "") -> int:
        """原子发布：新建批次 → 旧版本下线 → 新版本上线并绑定批次 → 批次生效。

        顺序不可颠倒：``uq_semantic_published`` 保证同一对象只有一个 published，
        必须先下线旧版本再上线新版本。
        """
        now = datetime.now()
        try:
            rel = SemanticRelease(
                tenant_id="default", label=self.next_release_label(),
                status=ReleaseStatus.draft, note=release_note, created_by=actor,
            )
            self.db.add(rel)
            self.db.flush()
            self.db.execute(
                update(SemanticObject)
                .where(
                    SemanticObject.obj_type == ObjType(obj_type),
                    SemanticObject.name == name,
                    SemanticObject.status == ObjStatus.published,
                    SemanticObject.version != ver,
                )
                .values(status=ObjStatus.deprecated)
            )
            self.db.execute(
                update(SemanticObject)
                .where(
                    SemanticObject.obj_type == ObjType(obj_type),
                    SemanticObject.name == name,
                    SemanticObject.version == ver,
                )
                .values(
                    status=ObjStatus.published, release_id=rel.release_id,
                    reviewed_by=actor, reviewed_at=now, review_comment=comment,
                )
            )
            self.db.execute(
                update(SemanticRelease)
                .where(SemanticRelease.release_id == rel.release_id)
                .values(status=ReleaseStatus.published, published_at=now)
            )
            self.db.commit()
            return rel.release_id
        except Exception:
            self.db.rollback()
            raise

    def update_status(self, obj_type: str, name: str, ver: int, status: str,
                      actor: str, comment: str = ""):
        """流转待审批/驳回状态；发布统一走 ``publish_version``。"""
        values = {"status": ObjStatus(status)}
        if status == "pending":
            values.update(submitted_by=actor, submitted_at=datetime.now())
        elif status == "rejected":
            values.update(reviewed_by=actor, reviewed_at=datetime.now(),
                          review_comment=comment)
        self.db.execute(
            update(SemanticObject)
            .where(
                SemanticObject.obj_type == ObjType(obj_type),
                SemanticObject.name == name,
                SemanticObject.version == ver,
            )
            .values(**values)
        )
        self.db.commit()

    def update_payload(self, obj_type: str, name: str, ver: int, payload: dict, actor: str,
                       comment: str = ""):
        self.db.execute(
            update(SemanticObject)
            .where(
                SemanticObject.obj_type == ObjType(obj_type),
                SemanticObject.name == name,
                SemanticObject.version == ver,
            )
            .values(payload=payload)
        )
        self.db.commit()
        self.add_event(obj_type, name, ver, "edit", actor, comment)

    def add_event(self, obj_type: str, name: str, ver: int, action: str,
                  actor: str = "", comment: str = ""):
        self.db.add(SemanticEvent(
            obj_type=ObjType(obj_type), name=name, version=ver,
            action=ApprovalAction(action), actor=actor, comment=comment,
        ))
        self.db.commit()
