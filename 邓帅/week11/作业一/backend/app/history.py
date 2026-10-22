"""问答历史持久化:backend/data/history.db(独立于 chinook.db)。

每次问答(无论成败)落库一条;列表按时间倒序,供前端左栏回放。
"""
import json
import sqlite3
import time
from pathlib import Path

from app.config import HISTORY_DB_PATH

# 模块级路径(测试 monkeypatch 用)
HISTORY_DB_PATH = HISTORY_DB_PATH

_COLUMNS = [
    "id", "question", "status", "tables", "sql", "summary", "error",
    "columns", "rows", "row_count", "truncated", "rewrite_rounds",
    "duration_ms", "steps", "created_at",
]


def _connect() -> sqlite3.Connection:
    Path(HISTORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(HISTORY_DB_PATH)


def init_db() -> None:
    """建表(幂等)并做轻量迁移(老库补列)。"""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                tables TEXT NOT NULL DEFAULT '[]',
                sql TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                columns TEXT NOT NULL DEFAULT '[]',
                rows TEXT NOT NULL DEFAULT '[]',
                row_count INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                rewrite_rounds INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                steps TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            )
            """
        )
        # 迁移:早期建的老库没有 error 列,补上(已存在则忽略)
        try:
            conn.execute("ALTER TABLE history ADD COLUMN error TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在


def save_record(record: dict) -> int:
    """落库一条问答记录,返回自增 id。list/dict 字段序列化为 JSON。"""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO history (
                question, status, tables, sql, summary, error, columns, rows,
                row_count, truncated, rewrite_rounds, duration_ms, steps, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["question"],
                record["status"],
                json.dumps(record.get("tables", []), ensure_ascii=False),
                record.get("sql", ""),
                record.get("summary", ""),
                record.get("error", ""),
                json.dumps(record.get("columns", []), ensure_ascii=False),
                json.dumps(record.get("rows", []), ensure_ascii=False),
                record.get("row_count", 0),
                int(record.get("truncated", False)),
                record.get("rewrite_rounds", 0),
                record.get("duration_ms", 0),
                json.dumps(record.get("steps", []), ensure_ascii=False),
                time.time(),
            ),
        )
        return int(cur.lastrowid or 0)


def list_records(limit: int = 50) -> list[dict]:
    """按时间倒序列出问答记录(JSON 字段反序列化)。"""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    records = []
    for row in rows:
        r = dict(row)
        r["tables"] = json.loads(r["tables"])
        r["columns"] = json.loads(r["columns"])
        r["rows"] = json.loads(r["rows"])
        r["steps"] = json.loads(r["steps"])
        r["truncated"] = bool(r["truncated"])
        records.append(r)
    return records
