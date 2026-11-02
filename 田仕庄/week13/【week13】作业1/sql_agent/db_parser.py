"""DBParser：数据库解析与安全执行（改造自课程参考 notebook）。

职责：
1. 元数据解析：表清单、字段、主外键关系、样例数据（含缓存）
2. SQL 安全校验：只读白名单（仅允许单条 SELECT / WITH 语句）
3. SQL 执行：只读连接 + 行数上限 + 执行超时（progress handler）
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from sql_agent import config

if TYPE_CHECKING:  # 仅在类型检查时导入，避免拖慢进程启动（pandas 约 1.1s）
    import pandas as pd

# 禁止出现的 SQL 关键词（词边界匹配）
FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "PRAGMA", "ATTACH", "DETACH", "VACUUM", "GRANT", "REVOKE",
    "REINDEX", "ANALYZE",
]

# 允许的语句开头
_ALLOWED_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# 词边界 forbidden 关键词
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)

# SQL 注释
_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)

# SQL 字符串字面量（含 '' 转义），校验前剥离，避免误杀 'DELETE' 这类合法字面量
_STRING_RE = re.compile(r"'(?:[^']|'')*'")

# 样例数据单元格最大长度（超长截断，节省 token）
_SAMPLE_CELL_MAX = 60


class SQLSafetyError(Exception):
    """SQL 未通过只读安全校验时抛出。"""


class DBParser:
    """数据库解析器：元数据 + 安全校验 + 只读执行。"""

    def __init__(self, db_url: Optional[str] = None) -> None:
        db_url = db_url or config.DATABASE_URL
        db_url = config.resolve_sqlite_url(db_url)

        self.db_url = db_url
        self.db_type = "sqlite" if db_url.startswith("sqlite") else "mysql"
        self.engine: Engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names: list[str] = [
            t for t in self.inspector.get_table_names()
            if not t.startswith("sqlite_")  # 过滤 sqlite 内部表
        ]

        # 外键关系（含正向与反向，便于上下文拼接）
        self.foreign_keys: list[dict] = []
        for table_name in self.table_names:
            for fk in self.inspector.get_foreign_keys(table_name):
                self.foreign_keys.append({
                    "constrained_table": table_name,
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

        # 字段元数据缓存 {table: {column: meta}}
        self._table_fields: dict[str, dict[str, dict]] = {
            t: {c["name"]: c for c in self.inspector.get_columns(t)}
            for t in self.table_names
        }

        # 每表 schema Markdown 片段缓存（表结构静态，只拼一次）
        self._schema_md_cache: dict[tuple[str, int], str] = {}
        # 每表主键列缓存
        self._pk_cache: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    def get_table_fields(self, table_name: str) -> "pd.DataFrame":
        """获取某张表的字段信息（名称/类型/可空/主键/默认值）。

        返回 DataFrame 以兼容参考实现；内部渲染走 _table_markdown，不依赖 pandas。
        """
        import pandas as pd  # 延迟导入：仅显式调用此 API 时才付出导入成本

        pk = set(self.inspector.get_pk_constraint(table_name)["constrained_columns"])
        rows = []
        for name, meta in self._table_fields[table_name].items():
            rows.append({
                "column": name,
                "type": str(meta.get("type")),
                "nullable": meta.get("nullable", True),
                "primary_key": name in pk,
            })
        return pd.DataFrame(rows)

    def get_data_relations(self) -> "pd.DataFrame":
        """获取外键关系表。"""
        import pandas as pd

        return pd.DataFrame(
            self.foreign_keys,
            columns=["constrained_table", "constrained_columns",
                     "referred_table", "referred_columns"],
        )

    def get_table_sample(self, table_name: str, n: int = 3) -> "pd.DataFrame":
        """获取某张表的前 n 行样例。"""
        import pandas as pd

        columns, rows = self._sample_rows(table_name, n)
        return pd.DataFrame(rows, columns=columns)

    def _sample_rows(self, table_name: str, n: int) -> tuple[list[str], list[tuple]]:
        """取前 n 行样例（原生元组，不经过 pandas）。"""
        with self.engine.connect() as conn:
            result = conn.execute(
                text(f'SELECT * FROM "{table_name}" LIMIT :n'), {"n": n}
            )
            return list(result.keys()), [tuple(r) for r in result.fetchall()]

    def _pk_columns(self, table_name: str) -> set[str]:
        """主键列名（按表缓存，避免重复反射）。"""
        if table_name not in self._pk_cache:
            self._pk_cache[table_name] = set(
                self.inspector.get_pk_constraint(table_name)["constrained_columns"]
            )
        return self._pk_cache[table_name]

    def get_row_count(self, table_name: str) -> int:
        """获取某张表的总行数（帮助 LLM 感知数据量级）。"""
        with self.engine.connect() as conn:
            return conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar_one()

    @staticmethod
    def _truncate_cell(v, max_len: int = _SAMPLE_CELL_MAX):
        """截断超长样例值，节省上下文 token。"""
        if v is None:
            return v
        s = str(v)
        return s if len(s) <= max_len else s[:max_len] + "…"

    @staticmethod
    def _md_table(headers: list[str], rows: list[tuple]) -> str:
        """把 (列名, 数据行) 渲染为 Markdown 表格（纯字符串拼接，不依赖 pandas）。

        数值列右对齐，其余左对齐，便于 LLM 与终端阅读。
        """
        def _fmt(v) -> str:
            if v is None:
                return ""
            if isinstance(v, float):
                # 避免二进制浮点尾差（105.92999999999999 -> 105.93）
                v = f"{v:g}"
            return str(v).replace("|", "\\|").replace("\n", " ")

        def _numeric(idx: int) -> bool:
            vals = [r[idx] for r in rows if r[idx] is not None]
            return bool(vals) and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals
            )

        aligns = [":--:" if _numeric(i) else ":--" for i in range(len(headers))]
        lines = [
            "| " + " | ".join(_fmt(h) for h in headers) + " |",
            "|" + "|".join(aligns) + "|",
        ]
        for r in rows:
            lines.append("| " + " | ".join(_fmt(v) for v in r) + " |")
        return "\n".join(lines)

    def _table_markdown(self, table_name: str, sample_rows: int) -> str:
        """单表 schema 片段（结构 + 行数 + 截断后的样例），带缓存。"""
        key = (table_name, sample_rows)
        if key in self._schema_md_cache:
            return self._schema_md_cache[key]

        parts = [f"## 表 {table_name}"]
        try:
            parts.append(f"数据量：约 {self.get_row_count(table_name)} 行")
        except Exception:
            pass

        # 字段表：直接由反射元数据拼装，不经过 pandas
        pk = self._pk_columns(table_name)
        field_rows = [
            (name, str(meta.get("type")), meta.get("nullable", True), name in pk)
            for name, meta in self._table_fields[table_name].items()
        ]
        parts.append(self._md_table(
            ["column", "type", "nullable", "primary_key"], field_rows
        ))

        if sample_rows > 0:
            try:
                columns, rows = self._sample_rows(table_name, sample_rows)
                rows = [tuple(self._truncate_cell(v) for v in r) for r in rows]
                parts.append(f"\n**样例数据（前{sample_rows}行）**：")
                parts.append(self._md_table(columns, rows))
            except Exception:  # 样例获取失败不影响主流程
                pass

        md = "\n".join(parts)
        self._schema_md_cache[key] = md
        return md

    def schema_markdown(self, table_names: Optional[list[str]] = None,
                        sample_rows: int = 3) -> str:
        """把指定表的结构 + 外键 + 样例拼装成 Markdown 上下文（供 LLM 使用）。

        单表片段有缓存，重复调用只做拼接。
        """
        table_names = table_names or self.table_names
        parts: list[str] = []

        if self.foreign_keys:
            parts.append("## 外键关系（表A.字段 -> 表B.字段）")
            for fk in self.foreign_keys:
                parts.append(
                    f"- {fk['constrained_table']}.{'.'.join(fk['constrained_columns'])}"
                    f" -> {fk['referred_table']}.{'.'.join(fk['referred_columns'])}"
                )
            parts.append("")

        for t in table_names:
            parts.append(self._table_markdown(t, sample_rows))
            parts.append("")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 安全校验与执行
    # ------------------------------------------------------------------
    @staticmethod
    def check_sql(sql: str) -> tuple[bool, str]:
        """只读安全校验：仅允许单条 SELECT/WITH 语句。

        返回 (是否通过, 原因)。
        """
        if not sql or not sql.strip():
            return False, "SQL 为空"

        # 去掉注释、剥离字符串字面量后再校验（避免 'DELETE' 这类合法值被误杀）
        stripped = _COMMENT_RE.sub("", sql)
        stripped = _STRING_RE.sub("''", stripped)
        stripped = stripped.strip().rstrip(";").strip()

        if not stripped:
            return False, "SQL 去除注释后为空"

        if not _ALLOWED_PREFIX.match(stripped):
            leading = stripped.split(None, 1)[0].upper() if stripped else ""
            return False, (
                f"仅允许 SELECT 或 WITH 开头的只读查询语句（当前语句以 {leading} 开头）"
            )

        m = _FORBIDDEN_RE.search(stripped)
        if m:
            return False, f"检测到禁止的关键词: {m.group(1).upper()}（只读 Agent 不允许写操作）"

        if ";" in stripped:
            return False, "不允许执行多条语句（检测到分号）"

        return True, "ok"

    def execute_sql(self, sql: str,
                    max_rows: Optional[int] = None,
                    timeout: Optional[float] = None
                    ) -> tuple[list[str], list[tuple], bool]:
        """安全执行 SQL 并返回 (列名, 数据行, 是否被截断)。

        自动做只读校验；结果超过 max_rows 截断；
        SQLite 通过 progress handler 实现语句级超时（默认 config.QUERY_TIMEOUT 秒）。
        校验失败或执行报错时抛出 SQLSafetyError / SQLAlchemyError。
        """
        ok, reason = self.check_sql(sql)
        if not ok:
            raise SQLSafetyError(reason)

        max_rows = max_rows or config.MAX_RESULT_ROWS
        timeout = timeout or config.QUERY_TIMEOUT
        with self.engine.connect() as conn:
            raw = getattr(conn.connection, "driver_connection", None)
            if raw is not None and hasattr(raw, "set_progress_handler"):
                deadline = time.monotonic() + timeout

                def _on_progress() -> int:
                    return 1 if time.monotonic() > deadline else 0  # 1 = 中止

                raw.set_progress_handler(_on_progress, 50_000)  # 每 5 万步检查一次
            try:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = result.fetchmany(max_rows + 1)
            finally:
                if raw is not None and hasattr(raw, "set_progress_handler"):
                    raw.set_progress_handler(None, 0)
        truncated = len(rows) > max_rows
        return columns, [tuple(r) for r in rows[:max_rows]], truncated

    def result_to_markdown(self, sql: str,
                           max_rows: Optional[int] = None) -> str:
        """执行 SQL 并把结果渲染为 Markdown 表格。"""
        columns, rows, truncated = self.execute_sql(sql, max_rows)
        if not rows:
            return "查询执行成功，但没有返回任何数据行。"
        md = self._md_table(columns, rows)
        if truncated:
            md += f"\n\n（结果超过 {max_rows} 行，仅显示前 {max_rows} 行）"
        return md
