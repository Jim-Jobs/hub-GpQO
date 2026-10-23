"""ORM 枚举：数据库存小写 value，避免 PG 原生枚举的升级成本。"""
from __future__ import annotations

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
    clarify = "clarify"


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
