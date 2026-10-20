"""权限上下文：行级规则（编译期注入 WHERE）与列级规则（拒绝/脱敏）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PermissionContext:
    user_id: str
    display_name: str = ""
    row_rules: list = field(default_factory=list)
    denied_columns: list = field(default_factory=list)
    masked_columns: list = field(default_factory=list)


DEFAULT_USER = PermissionContext(user_id="anonymous", display_name="匿名用户")


def load_permissions(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = {}
    for u in data.get("users", []):
        out[u["user_id"]] = PermissionContext(
            user_id=u["user_id"],
            display_name=u.get("display_name", u["user_id"]),
            row_rules=u.get("row_rules", []),
            denied_columns=u.get("denied_columns", []),
            masked_columns=u.get("masked_columns", []),
        )
    return out
