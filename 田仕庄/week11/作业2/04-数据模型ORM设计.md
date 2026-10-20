# 04 · 数据模型 ORM 设计

> 配套《03-技术设计文档》第 8 节。本文把第 8 节的目标数据模型落成**可直接使用的 SQLAlchemy 2.0 ORM 定义**，
> 并给出 payload 校验模型、关键操作写法与迁移路径。
>
> - ORM：SQLAlchemy 2.0（Declarative + `Mapped[]` 类型注解）
> - 校验：Pydantic v2
> - 主库：PostgreSQL 14+（`JSONB`、部分唯一索引、`pgvector` 可选）
> - 原型：SQLite（同一套模型，通过类型变体与条件索引兼容）
> - 表结构与命名以《03》8.3–8.10 为准，本文不重复业务语义

---

## 1. 约定

| 约定 | 实现方式 |
| --- | --- |
| 多租户 | `TenantMixin` 统一提供 `tenant_id`，默认 `'default'` |
| 主键 | `BigInteger`，SQLite 下降级为 `Integer`（`with_variant`），保证自增可用 |
| JSON | `JSON` + `with_variant(JSONB, "postgresql")`，PG 走二进制 JSONB |
| 时间 | `DateTime(timezone=True)`，服务端默认 `now()` |
| 版本不可变 | `semantic_object` 只插入不更新 payload；仅允许状态列流转 |
| 命名 | 统一 `naming_convention`，便于 Alembic 生成稳定约束名 |
| 枚举 | `Enum(native_enum=False)`，存小写值，避免 PG 枚举类型升级困难 |

---

## 2. 基础层

```python
# chatbi/db/base.py
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, DateTime, Index, Integer, MetaData, String, func, text,
)
from sqlalchemy import Enum as SAEnum
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
```

### 2.1 枚举

```python
# chatbi/db/enums.py
import enum


class ObjType(str, enum.Enum):
    model = "model"
    metric = "metric"
    dimension = "dimension"


class ObjStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    published = "published"
    rejected = "rejected"
    deprecated = "deprecated"


class ReleaseStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    superseded = "superseded"


class TurnStatus(str, enum.Enum):
    ok = "ok"
    clarify = "clarify"
    invalid = "invalid"
    denied = "denied"
    error = "error"


class RewriteMode(str, enum.Enum):
    new = "new"
    borrow = "borrow"
    followup = "followup"
    clarify_pick = "clarify_pick"


class ClarifyStatus(str, enum.Enum):
    pending = "pending"
    resolved = "resolved"
    expired = "expired"


class ClarifyReason(str, enum.Enum):
    no_metric = "no_metric"
    ambiguous_metric = "ambiguous_metric"
    dim_unsupported = "dim_unsupported"
    low_conf = "low_conf"


class ApprovalAction(str, enum.Enum):
    create = "create"
    edit = "edit"
    submit = "submit"
    approve = "approve"
    reject = "reject"
    rollback = "rollback"
    import_ = "import"
    deprecate = "deprecate"


class SubjectType(str, enum.Enum):
    user = "user"
    role = "role"


class RuleType(str, enum.Enum):
    row = "row"
    column = "column"


class RuleAction(str, enum.Enum):
    filter = "filter"
    deny = "deny"
    mask = "mask"
    aggregate = "aggregate"
```

### 2.2 实体与表映射总览

| 表 | ORM 类 | 域 | 原型是否已落地 |
| --- | --- | --- | --- |
| `tenant` | `Tenant` | 组织 | ⬜ |
| `app_user` | `AppUser` | 组织 | ⬜（YAML 用户） |
| `role` / `user_role` | `Role` / `UserRole` | 组织 | ⬜（YAML 角色） |
| `data_source` | `DataSource` | 数据源 | ⬜ |
| `semantic_release` | `SemanticRelease` | 语义层 | ✅ |
| `semantic_object` | `SemanticObject` | 语义层 | ✅ |
| `semantic_event` | `SemanticEvent` | 语义层 | ✅ |
| `permission_rule` | `PermissionRule` | 权限 | ⬜（YAML） |
| `chat_session` | `ChatSession` | 会话 | ✅ |
| `chat_turn` | `ChatTurn` | 会话 | ✅ |
| `chat_clarify` | `ChatClarify` | 会话 | ✅ |
| `query_audit` | `QueryAudit` | 审计 | ✅ |
| `eval_case` / `eval_run` / `eval_result` | `EvalCase` / `EvalRun` / `EvalResult` | 评测 | ✅（JSONL 仅作导入源） |
| `feedback` | `Feedback` | 反馈 | ⬜ |
| `semantic_term` | `SemanticTerm` | 词表 | ⬜（YAML 同义词） |
| `semantic_embedding` | `SemanticEmbedding` | 召回 | ⬜（可选，PG only） |

---

## 3. 组织与数据源

```python
# chatbi/db/models/org.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, BigIntPK, CreatedAtMixin, TenantMixin


class Tenant(Base, CreatedAtMixin):
    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    users: Mapped[list["AppUser"]] = relationship(back_populates="tenant")


class AppUser(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "app_user"

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
    """复合主键关联表，不继承 TenantMixin（租户由两端约束）。"""

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
```

---

## 4. 语义层元数据

```python
# chatbi/db/models/semantic.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
from ..enums import ApprovalAction, ObjStatus, ObjType, ReleaseStatus


class SemanticRelease(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "semantic_release"
    __table_args__ = (UniqueConstraint("tenant_id", "label", name="uq_release_label"),)

    release_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)     # 2026-10-20.1
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
```

---

## 5. 权限

```python
# chatbi/db/models/permission.py
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
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
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
```

> `CheckConstraint` 中 `rule_type` 存的是枚举 **value**（`'row'`），与 `values_callable` 一致。
> 注意 `priority` / `enabled` 使用类型推断（`Mapped[int]` → `Integer`），也可显式写 `Integer()`。

---

## 6. 会话与澄清

```python
# chatbi/db/models/chat.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin, enum_col
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
    clarifies: Mapped[list["ChatClarify"]] = relationship(back_populates="session")


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
```

---

## 7. 审计

```python
# chatbi/db/models/audit.py
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import CHAR, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin


class QueryAudit(Base, TenantMixin, CreatedAtMixin):
    """PG 上以 created_at 按月分区；ORM 映射父表，SQLite 建普通表。"""

    __tablename__ = "query_audit"
    __table_args__ = (
        Index("ix_query_audit_user", "tenant_id", "user_id", "created_at"),
    )

    audit_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(64))
    turn_index: Mapped[Optional[int]] = mapped_column(Integer)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    dsl: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)
    sql_text: Mapped[Optional[str]] = mapped_column(Text)
    sql_hash: Mapped[Optional[str]] = mapped_column(CHAR(64))
    release_id: Mapped[Optional[int]] = mapped_column(BigIntPK)
    perm_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType)

    row_count: Mapped[Optional[int]] = mapped_column(Integer)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
```

---

## 8. 评测与反馈

```python
# chatbi/db/models/eval.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, BigIntPK, CreatedAtMixin, JSONType, TenantMixin


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
    rating: Mapped[Optional[int]] = mapped_column(Integer)   # 1 / -1
    corrected_question: Mapped[Optional[str]] = mapped_column(Text)
    corrected_sql: Mapped[Optional[str]] = mapped_column(Text)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
```

---

## 9. 词表与向量

```python
# chatbi/db/models/recall.py
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, CreatedAtMixin, TenantMixin


class SemanticTerm(Base, TenantMixin, CreatedAtMixin):
    __tablename__ = "semantic_term"
    __table_args__ = (
        UniqueConstraint("tenant_id", "term", "target_type", "target_name", name="uq_term_target"),
        Index(
            "ix_semantic_term_lookup", "tenant_id", "term",
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    term_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # metric|dimension|dimension_value
    target_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_value: Mapped[Optional[str]] = mapped_column(String(256))
    weight: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
```

向量表单独放一个模块，只在使用 pgvector 时导入：

```python
# chatbi/db/models/embedding_pg.py（可选依赖：pip install pgvector）
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, TenantMixin


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


# HNSW 索引需在迁移脚本中单独创建：
# CREATE INDEX ix_embedding_hnsw ON semantic_embedding
#   USING hnsw (embedding vector_cosine_ops);
```

---

## 10. Payload 校验模型（Pydantic v2）

`semantic_object.payload` 是异构 JSON，写入前必须按 `obj_type` 校验，读取时再反序列化为强类型。

```python
# chatbi/semantic/payloads.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chatbi.db.enums import ObjType


class FilterSpec(BaseModel):
    dimension: str
    op: Literal["eq", "neq", "in", "gt", "lt", "between", "like"] = "eq"
    value: object


class JoinField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class JoinSpec(BaseModel):
    to_model: str
    type: Literal["many_to_one", "one_to_many", "many_to_many"]
    on: list[JoinField]


class ModelPayload(BaseModel):
    display_name: str
    physical_table: str
    primary_key: str | None = None
    time_column: str | None = None
    joins: list[JoinSpec] = Field(default_factory=list)
    default_filters: list[FilterSpec] = Field(default_factory=list)
    owner: str = ""


class MetricFormat(BaseModel):
    unit: str = ""
    decimals: int = 2


class MetricPayload(BaseModel):
    display_name: str
    metric_type: Literal["atomic", "composite"]        # derived 本期不支持
    model: str
    expression: str
    synonyms: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    time_column: str | None = None
    available_dimensions: list[str] = Field(default_factory=list)
    default_filters: list[FilterSpec] = Field(default_factory=list)
    format: MetricFormat = Field(default_factory=MetricFormat)
    description: str = ""
    owner: str = ""


class DimensionPayload(BaseModel):
    display_name: str
    model: str
    expression: str | None = None
    dim_type: Literal["categorical", "time", "numeric"] = "categorical"
    hierarchy: list[str] = Field(default_factory=list)
    value_mapping: dict[str, list[str]] = Field(default_factory=dict)
    synonyms: list[str] = Field(default_factory=list)
    owner: str = ""


PAYLOAD_MODELS: dict[ObjType, type[BaseModel]] = {
    ObjType.model: ModelPayload,
    ObjType.metric: MetricPayload,
    ObjType.dimension: DimensionPayload,
}


def validate_payload(obj_type: ObjType, payload: dict) -> dict:
    """校验并归一化 payload；校验失败直接抛 ValidationError，不允许写库。"""
    return PAYLOAD_MODELS[obj_type].model_validate(payload).model_dump(by_alias=True, exclude_none=True)
```

---

## 11. 关键操作写法

### 11.1 读取线上版本（运行时唯一入口）

```python
from sqlalchemy import select

def load_published(db, tenant_id: str, obj_type: ObjType, name: str) -> SemanticObject | None:
    return db.scalar(
        select(SemanticObject).where(
            SemanticObject.tenant_id == tenant_id,
            SemanticObject.obj_type == obj_type,
            SemanticObject.name == name,
            SemanticObject.status == ObjStatus.published,
        )
    )


def load_all_published(db, tenant_id: str) -> list[SemanticObject]:
    return list(db.scalars(
        select(SemanticObject)
        .where(SemanticObject.tenant_id == tenant_id,
               SemanticObject.status == ObjStatus.published)
        .order_by(SemanticObject.obj_type, SemanticObject.name)
    ))
```

### 11.2 发布事务（含唯一线上版本约束）

```python
from sqlalchemy import update

def publish(db, tenant_id: str, obj_type: ObjType, name: str, version: int, release_id: int):
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
        .values(status=ObjStatus.published, release_id=release_id, reviewed_at=func.now())
    )
    db.execute(
        update(SemanticRelease)
        .where(SemanticRelease.release_id == release_id)
        .values(status=ReleaseStatus.published, published_at=func.now())
    )
```

> 三步必须在**同一事务**内完成（`with SessionLocal.begin()`）。
> 顺序不能颠倒：先下线旧版本，再上线新版本，否则会撞 `uq_semantic_published`。

### 11.3 回滚（新建草稿，不删历史）

```python
def rollback(db, tenant_id: str, obj_type: ObjType, name: str, to_version: int, actor: str) -> SemanticObject:
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
```

### 11.4 会话滑动续期

```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import update

def touch_session(db, session_id: str, ttl_seconds: int = 1800) -> bool:
    now = datetime.now(timezone.utc)
    res = db.execute(
        update(ChatSession)
        .where(ChatSession.session_id == session_id, ChatSession.expires_at > now)
        .values(updated_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
    )
    return res.rowcount > 0
```

### 11.5 取最近一次成功轮（上下文继承基线）

```python
def last_ok_turn(db, session_id: str) -> ChatTurn | None:
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
```

### 11.6 澄清接续

```python
def resolve_clarify(db, clarify_id: str, choice: dict) -> ChatClarify | None:
    c = db.get(ChatClarify, clarify_id)
    if c is None or c.status != ClarifyStatus.pending:
        return None
    if c.expires_at <= datetime.now(timezone.utc):
        c.status = ClarifyStatus.expired
        return None
    c.status = ClarifyStatus.resolved
    c.resolved_choice = choice
    return c
```

---

## 12. 索引与约束清单

| 表 | 类型 | 定义 | 目的 |
| --- | --- | --- | --- |
| `semantic_object` | 唯一约束 | `(tenant_id, obj_type, name, version)` | 版本不可重复 |
| `semantic_object` | 部分唯一索引 | `(tenant_id, obj_type, name) WHERE status='published'` | 同时只有一个线上版本 |
| `semantic_object` | 部分索引 | `(tenant_id, status) WHERE status='pending'` | 待审列表 |
| `semantic_object` | 普通索引 | `(tenant_id, obj_type, name, version)` | 版本历史查询 |
| `semantic_event` | 普通索引 | `(tenant_id, obj_type, name, version, event_id)` | 审计链回溯 |
| `permission_rule` | CHECK | 行级/列级字段组合互斥 | 防止脏规则 |
| `permission_rule` | 部分索引 | `(tenant_id, subject_type, subject_id) WHERE enabled` | 权限装配 |
| `chat_session` | 普通索引 | `(tenant_id, user_id, updated_at)` | 会话列表 |
| `chat_turn` | 唯一约束 | `(session_id, turn_index)` | 轮次幂等 |
| `chat_clarify` | 部分索引 | `(session_id) WHERE status='pending'` | 取待澄清 |
| `query_audit` | 普通索引 | `(tenant_id, user_id, created_at)` | 审计检索 |
| `semantic_term` | 唯一约束 | `(tenant_id, term, target_type, target_name)` | 词条唯一 |
| `semantic_term` | 部分索引 | `(tenant_id, term) WHERE status='active'` | 词表召回 |
| `eval_result` | 普通索引 | `(run_id, passed)` | 失败用例筛选 |

---

## 13. 初始化顺序与迁移

**建表顺序**（满足外键依赖）：

```
tenant → app_user → role → user_role → data_source
       → semantic_release → semantic_object → semantic_event
       → permission_rule
       → chat_session → chat_turn → chat_clarify
       → query_audit → eval_case → eval_run → eval_result → feedback
       → semantic_term → semantic_embedding(可选)
```

- `Base.metadata.create_all(engine)` 会按依赖自动排序，但**分区表与 HNSW 索引需手写迁移**。
- 生产使用 Alembic：`alembic revision --autogenerate -m "init semantic model"`，再人工补：
  - `query_audit` 的 `PARTITION BY RANGE (created_at)`
  - `semantic_embedding` 的 `USING hnsw`
  - 部分唯一索引（autogenerate 对 `postgresql_where` 支持有限，需核对）
- SQLite 原型：`JSONB` 退化为 `TEXT`、`BigInteger` 退化为 `INTEGER`，其余无需改动。

---

## 14. 与现有 `metastore.py` 的衔接

现有实现是手写 SQL（`insert_version` / `update_status` / `latest_published` 等），建议分四步替换，每步都可独立回归：

| 步骤 | 变更 | 风险 | 回归 |
| --- | --- | --- | --- |
| 1 ✅ | `semantic_object` 增 `tenant_id`(默认 `default`)、`prev_version` 两列 | 低（纯新增，带默认值） | `semantic_admin.py list/history` |
| 2 ✅ | 新增 `semantic_release`，发布时写 `release_id` | 低 | `semantic_admin.py demo` |
| 3 ✅ | 用 ORM 替换 `insert_version` / `update_status` 等函数，保持函数签名不变 | 中 | 单轮 EX + 多轮 EX |
| 4 ✅ | 落地 `chat_*` / `query_audit` / `eval_*`，把内存会话与 JSONL 评测迁入库 | 中高 | 两套评测重跑 |

> 关键约束：缓存 key 现已可用 `release_id` 作为失效锚点（发布批次已落地）。

**当前落地状态**

ORM 代码已按本文落地到 `chatbi/db/`：

| 文件 | 内容 |
| --- | --- |
| `base.py` | `Base`、`BigIntPK` / `JSONType` 类型变体、`TenantMixin` / `CreatedAtMixin`、`enum_col` |
| `enums.py` | 全部枚举 |
| `models/*.py` | 17 个实体（`embedding_pg.py` 为可选依赖，不默认导入） |
| `payloads.py` | 三类 payload 的 Pydantic 模型与 `validate_payload` |
| `repo.py` | 线上版本读取、发布事务、回滚、会话续期、澄清接续 |
| `engine.py` | 引擎/会话工厂、`init_db`、`session_scope` |

```bash
python -m chatbi.db --init     # 建表（幂等）并打印表清单
```

步骤 1–3 落地情况如下，**ORM 库与运行时库已合并为同一个 `data/chatbi_meta.db`**
（`CHATBI_META_DB` 可改路径，`CHATBI_ORM_DB` 仍可单独覆盖 ORM 侧默认值）：

| 变更 | 实现 |
| --- | --- |
| 表结构 | `semantic_object` 含 `tenant_id`(默认 `default`)、`prev_version`、`release_id`；`semantic_release` 独立成表 |
| 批次 | 标签形如 `2026-10-20.1`（同日按序号递增），`draft → published` |
| 发布路径 | `approve` 走 `MetaStore.publish_version()`：同一事务内「建批次 → 旧版本 deprecated → 新版本 published 并绑定 `release_id` → 批次生效」 |
| 导入路径 | `import_yaml` 整批共用一个批次，导入完成后一次性置为 `published` |
| 溯源 | 基于线上版本改草稿、回滚，均写 `prev_version` |
| 不变式 | 部分唯一索引 `uq_semantic_published (obj_type, name) WHERE status='published'` 兜底「同一对象只能有一个线上版本」 |
| 历史数据 | 迁移前已发布/已下线版本统一挂到 `legacy` 批次，保证 `published ⇒ release_id 非空` |
| 代码 | `metastore.MetaStore` 改为 ORM 适配层：对外方法（`insert_version` / `update_status` / `list_objects` …）签名不变，内部走 SQLAlchemy Session 与 ORM 模型；`ddl()` 由 ORM metadata 生成 |

步骤 4 也已落地，四步迁移全部完成：

| 变更 | 实现 |
| --- | --- |
| 会话 | `session.SessionStore` 以 SQLite 为后端，`chat_session` / `chat_turn` / `chat_clarify` 落库；TTL 用 `expires_at` 滑动续期，容量按 `updated_at` 淘汰；`Session`/`Turn` 仍是对外视图对象 |
| 澄清 | 澄清轮写 `chat_clarify(pending)`，被 `clarify_pick` 接续后置 `resolved` 并记录所选问题 |
| 审计 | `ChatBI._audit()` 每次问数写 `query_audit`：DSL、SQL、`sql_hash`、`release_id`、权限快照、行数与耗时 |
| 评测 | `eval.py` 把 JSONL 导入 `eval_case`（打 `src:<文件>` 标签、按问题去重），运行写 `eval_run` + `eval_result`；JSONL 仅作导入源 |
| 并发 | SQLite 开启 WAL + `busy_timeout`，读数与写入互不阻塞；写事务均显式提交，避免长事务占锁 |

---

## 15. 已确认口径

| 决策 | 采用方案 | 说明 |
| --- | --- | --- |
| ID 类型 | 自增 `BIGSERIAL`（SQLite 降级 `INTEGER`） | 单实例写入足够；多实例前再切 ULID/雪花 |
| 会话持久化粒度 | `row_sample` 入库，最多 100 行 | 指代消解需要真实取值；超限截断 |
| 审计分区 | MVP **不分区**，单表 + 索引 | 数据量小；上 PG 生产库后再加按月分区 |
| 枚举存储 | 存小写 value 的 `VARCHAR` | 避免 PG 原生枚举的升级成本，迁移更简单 |

语法与建表已实测：17 个 Python 代码块全部通过语法检查，18 张表在 SQLite 上
`create_all` 成功；发布/回滚/唯一线上版本约束/会话续期/澄清接续均通过冒烟测试。
