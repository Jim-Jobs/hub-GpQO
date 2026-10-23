"""SQL 沙箱执行：只读连接、禁用写操作、行数限制。"""
from __future__ import annotations

import re
import sqlite3
import time

from . import config

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|vacuum|grant|revoke)\b",
    re.IGNORECASE,
)


class ExecutionError(Exception):
    """SQL 执行或安全拦截错误。"""


def _guard(sql: str):
    stripped = re.sub(r"^\s*(--[^\n]*\n\s*)*", "", sql).strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        raise ExecutionError("安全拦截：仅允许 SELECT 查询")
    if _FORBIDDEN.search(stripped):
        raise ExecutionError("安全拦截：SQL 中包含禁止的关键字")


def execute(db_path, sql: str, max_rows: int | None = None) -> dict:
    """执行 SQL 并返回 {columns, rows, elapsed_ms, truncated}。"""
    _guard(sql)
    max_rows = max_rows or config.MAX_ROWS
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    t0 = time.perf_counter()
    try:
        con.execute("PRAGMA query_only=ON")
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        truncated = len(rows) == max_rows
    except sqlite3.Error as e:  # pragma: no cover - 依赖具体 SQL
        raise ExecutionError(f"SQL 执行失败: {e}") from e
    finally:
        con.close()
    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "truncated": truncated,
    }


def scalar(db_path, sql: str):
    """执行单值查询，返回第一行第一列。"""
    return execute(db_path, sql, max_rows=1)["rows"][0][0]
