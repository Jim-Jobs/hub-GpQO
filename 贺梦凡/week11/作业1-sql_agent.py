"""Chinook SQL Agent.

参考 sql-agent.ipynb 的数据库解析，把自然语言问题走完一条只读 SQL 工作流：

1. 数据库解析
2. table 选择
3. sql 生成
4. sql 校验
5. sql 改写
6. sql 执行
7. 结果汇总

未设置 OPENAI_API_KEY 时使用本机 Ollama（qwen3:0.6b）。
设置 OPENAI_API_KEY 后走云端，默认 base_url 为百炼兼容接口，模型为 qwen-plus。
可用 OPENAI_BASE_URL、SQL_AGENT_MODEL 覆盖。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pandas as pd
from openai import OpenAI
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DEMO_DIR = Path(__file__).resolve().parent
DEFAULT_DB = DEMO_DIR / "chinook.db"

def resolve_llm_config(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """优先使用环境变量里的云端模型，否则使用本机 Ollama。"""
    env_key = os.getenv("OPENAI_API_KEY")
    env_base = os.getenv("OPENAI_BASE_URL")
    env_model = os.getenv("SQL_AGENT_MODEL")
    if api_key or env_key:
        return {
            "api_key": api_key or env_key,
            "base_url": base_url or env_base or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": model or env_model or "qwen-plus",
            "extra_body": None,
        }
    return {
        "api_key": "ollama",
        "base_url": base_url or env_base or "http://127.0.0.1:11434/v1",
        "model": model or env_model or "qwen3:0.6b",
        "extra_body": {"think": False, "options": {"num_ctx": 16384}},
    }

TABLE_HINTS = {
    "artists": "艺术家或乐队",
    "albums": "专辑，属于某位艺术家",
    "tracks": "曲目，属于专辑，含流派、媒体类型、时长和单价",
    "genres": "音乐流派",
    "media_types": "音频媒体类型",
    "playlists": "播放列表",
    "playlist_track": "播放列表和曲目的多对多关系",
    "customers": "客户，含国家、城市，以及负责该客户的员工",
    "employees": "员工，ReportsTo 指向直属上级",
    "invoices": "发票，含开票日期、账单国家和发票总额",
    "invoice_items": "发票明细，记录购买的曲目、单价和数量",
}

JOIN_GUIDE = """
Chinook 是一家数字音乐商店，表之间的业务关系如下：
- artists 一对多 albums，albums 一对多 tracks
- tracks 多对一 genres，tracks 多对一 media_types
- 购买记录：invoices 一对多 invoice_items，invoice_items 多对一 tracks
- 曲目销售额 = invoice_items.UnitPrice * invoice_items.Quantity，不要用 invoices.Total 代替单曲销售额
- customers 一对多 invoices；customers.SupportRepId 指向 employees.EmployeeId
- employees.ReportsTo 指向另一位员工的 EmployeeId
- playlists 与 tracks 通过 playlist_track 形成多对多
""".strip()


def _to_sqlite_url(db: str | Path) -> str:
    raw = str(db)
    if "://" in raw:
        return raw
    return "sqlite:///" + Path(raw).resolve().as_posix()


class DBParser:
    """解析数据库结构、字段统计、外键和样例，并负责 SQL 校验与执行。"""

    def __init__(self, db_url: str) -> None:
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            raise ValueError(f"暂不支持的数据库地址: {db_url}")

        self.db_url = db_url
        self.engine: Engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = [
            name
            for name in self.inspector.get_table_names()
            if not name.lower().startswith("sqlite_")
        ]

        self._table_fields: dict[str, dict] = {}
        self.foreign_keys: list[dict] = []
        self._table_sample: dict[str, pd.DataFrame] = {}

        with self.engine.connect() as conn:
            for table_name in self.table_names:
                print(f"Table -> {table_name}")
                self._profile_table(conn, table_name)

    def _profile_table(self, conn, table_name: str) -> None:
        self.foreign_keys.extend(
            {
                "constrained_table": table_name,
                "constrained_columns": item["constrained_columns"],
                "referred_table": item["referred_table"],
                "referred_columns": item["referred_columns"],
            }
            for item in self.inspector.get_foreign_keys(table_name)
        )

        table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
        table_columns = self.inspector.get_columns(table_name)
        self._table_fields[table_name] = {col["name"]: dict(col) for col in table_columns}

        for column_meta in table_columns:
            column_name = column_meta["name"]
            column = getattr(table_instance.columns, column_name)
            profile = self._table_fields[table_name][column_name]

            distinct_count = conn.execute(
                select(func.count(func.distinct(column)))
            ).scalar()
            profile["distinct"] = distinct_count

            field_type = str(profile["type"]).lower()
            if "text" in field_type or "char" in field_type:
                top_row = conn.execute(
                    select(column, func.count().label("count"))
                    .group_by(column)
                    .order_by(func.count().desc())
                    .limit(1)
                ).fetchone()
                profile["mode"] = None if top_row is None else top_row[0]
            else:
                profile["mode"] = None

            profile["nan_count"] = conn.execute(
                select(func.count()).where(column.is_(None))
            ).scalar()
            profile["max"] = conn.execute(select(func.max(column))).scalar()
            profile["min"] = conn.execute(select(func.min(column))).scalar()

            samples = [
                str(item[0])
                for item in conn.execute(select(column).limit(10)).all()
                if item[0] is not None
            ]
            profile["random"] = list(dict.fromkeys(samples))[:3]

        columns = [col["name"] for col in table_columns]
        sample_row = conn.execute(select(table_instance).limit(1)).fetchone()
        if sample_row is None:
            self._table_sample[table_name] = pd.DataFrame(columns=columns)
        else:
            self._table_sample[table_name] = pd.DataFrame([dict(sample_row._mapping)])

    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        return self._table_sample[table_name]

    def schema_text(self, table_names: list[str]) -> str:
        """把选中表的字段统计和一行样例整理成提示词。"""
        blocks = []
        for table_name in table_names:
            fields = self.get_table_fields(table_name)
            hint = TABLE_HINTS.get(table_name, "")
            lines = [f"表 {table_name}" + (f"（{hint}）" if hint else "") + ":"]
            for column_name, row in fields.iterrows():
                examples = row.get("random") or []
                example_text = f"，示例={examples}" if examples else ""
                lines.append(
                    f"- {column_name} {row['type']}，distinct={row['distinct']}，"
                    f"min={row['min']}，max={row['max']}{example_text}"
                )
            sample = self.get_table_sample(table_name)
            lines.append("样例:")
            lines.append(sample.to_markdown(index=False) if not sample.empty else "(空表)")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def catalog_text(self) -> str:
        lines = []
        for table_name in self.table_names:
            columns = list(self.get_table_fields(table_name).index)
            hint = TABLE_HINTS.get(table_name, "")
            suffix = f" — {hint}" if hint else ""
            lines.append(f"- {table_name}({', '.join(columns)}){suffix}")
        return "\n".join(lines)

    def relation_text(self, table_names: list[str] | None = None) -> str:
        relations = self.get_data_relations()
        if relations.empty:
            return "无外键"
        selected = set(table_names) if table_names else None
        lines = []
        for _, row in relations.iterrows():
            left = row["constrained_table"]
            right = row["referred_table"]
            if selected is not None and left not in selected and right not in selected:
                continue
            left_cols = ", ".join(row["constrained_columns"])
            right_cols = ", ".join(row["referred_columns"])
            lines.append(f"- {left}.{left_cols} -> {right}.{right_cols}")
        return "\n".join(lines) if lines else "无外键"

    def check_sql(self, sql: str) -> tuple[bool, str]:
        """用语法和只读规则校验 SQL，不返回查询结果。"""
        statement = _normalize_sql(sql)
        safe, message = _is_safe_select(statement)
        if not safe:
            return False, message
        try:
            with self.engine.connect() as conn:
                conn.execute(text("EXPLAIN " + statement)).fetchall()
                conn.rollback()
            return True, "ok"
        except Exception as exc:
            # 只把数据库返回的错误交给改写步骤，避免整段堆栈干扰模型。
            detail = str(exc).split("[SQL:", 1)[0].strip()
            return False, detail or str(exc)

    def execute_sql(self, sql: str, max_rows: int = 100) -> pd.DataFrame:
        """执行只读 SQL，并限制返回给模型的行数。"""
        statement = _normalize_sql(sql)
        safe, message = _is_safe_select(statement)
        if not safe:
            raise ValueError(message)
        with self.engine.connect() as conn:
            result = conn.execute(text(statement))
            columns = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
            conn.rollback()
        truncated = len(rows) > max_rows
        frame = pd.DataFrame(rows[:max_rows], columns=columns)
        frame.attrs["truncated"] = truncated
        return frame


class SQLAgent:
    """自然语言问题 -> 选表 -> 生成 SQL -> 校验/改写 -> 执行 -> 汇总。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        model: str | None = None,
        max_retry: int = 4,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        target = DEFAULT_DB if db_path is None else Path(db_path)
        if "://" not in str(db_path or "") and not target.exists():
            raise FileNotFoundError(f"找不到数据库文件: {target}")

        config = resolve_llm_config(model=model, api_key=api_key, base_url=base_url)
        self.model = config["model"]
        self.base_url = config["base_url"]
        self.extra_body = config["extra_body"]
        self.max_retry = max_retry
        self.parser = DBParser(_to_sqlite_url(target if db_path is None else db_path))
        self.client = OpenAI(api_key=config["api_key"], base_url=self.base_url)
        print(f"模型: {self.model} @ {self.base_url}")

    def llm(self, prompt: str, temperature: float = 0.1) -> str:
        kwargs = {}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            top_p=0.7,
            max_tokens=1024,
            **kwargs,
        )
        content = completion.choices[0].message.content or ""
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    def select_tables(self, question: str) -> dict[str, Any]:
        prompt = f"""你是数据库专家。请判断回答用户问题必须使用哪些表。
只输出 JSON，不要输出其他内容，格式为 {{"tables": ["表名"], "reason": "一句话理由"}}。
表名必须来自下面的清单。能回答问题时尽量少选表；需要跨表指标时把连接链路中的表都选上。

{JOIN_GUIDE}

数据表清单：
{self.parser.catalog_text()}

外键：
{self.parser.relation_text()}

用户问题：{question}
"""
        raw = self.llm(prompt, temperature=0.1)
        tables = self._canon_tables(_parse_tables(raw))
        reason = _parse_reason(raw)
        if not tables:
            mentioned = self._canon_tables(_mentioned_tables(raw, self.parser.table_names))
            if mentioned and len(mentioned) <= 6:
                tables = mentioned
                reason = reason or "从模型回答里识别出表名"
        if not tables:
            tables = self._keyword_tables(question)
            reason = "模型没有返回有效表名，已按问题关键词选择"
        required = self._required_tables(question)
        expanded = self._connect_tables(list(dict.fromkeys(tables + required)))
        return {
            "tables": tables,
            "expanded_tables": expanded,
            "reason": reason,
            "raw": raw,
        }

    def generate_sql(self, question: str, table_names: list[str]) -> str:
        prompt = f"""你是 SQLite 专家。根据用户问题写一条查询。
只输出一个 ```sql 代码块，不要解释。

要求：
- 只使用给定的表和字段，SQLite 语法
- 只写一条 SELECT 或 WITH 查询
- 统计问题写清 GROUP BY，排序问题写清 ORDER BY
- 排行榜的 LIMIT 用问题里的名次；只有查看明细时才写 LIMIT 50
- 销售额使用 SUM(invoice_items.UnitPrice * invoice_items.Quantity)
- 艺术家和曲目之间必须经过 albums，tracks 上没有 ArtistId
- 列名使用可读的英文别名

{JOIN_GUIDE}

{_sql_hint(question)}

用户问题：{question}

可用表：
{self.parser.schema_text(table_names)}

外键：
{self.parser.relation_text(table_names)}
"""
        return _extract_sql(self.llm(prompt, temperature=0.1))

    def validate_sql(self, sql: str) -> tuple[bool, str]:
        return self.parser.check_sql(sql)

    def check_answer_sql(
        self, question: str, sql: str, table_names: list[str]
    ) -> tuple[bool, str]:
        """先做语法和只读校验，再确认 SQL 仍在回答原问题。"""
        valid, message = self.validate_sql(sql)
        if not valid:
            return False, message
        missing = [
            name
            for name in self._required_tables(question)
            if not re.search(rf"\b{re.escape(name)}\b", sql, flags=re.I)
        ]
        if missing:
            return False, (
                "SQL 没有使用问题对应的表: "
                + ", ".join(missing)
                + "。请继续回答原问题，不要改成其他查询。"
            )
        if "销售" in question and "artists" in table_names:
            join_path = ["artists", "albums", "tracks", "invoice_items"]
            missing_path = [
                name
                for name in join_path
                if name in table_names
                and not re.search(rf"\b{name}\b", sql, flags=re.I)
            ]
            if missing_path:
                return False, (
                    "艺术家销售额必须沿 artists -> albums -> tracks -> invoice_items 连接，"
                    "SQL 缺少: " + ", ".join(missing_path)
                )
        if _needs_aggregate(question) and not re.search(
            r"\b(count|sum|avg|max|min)\b", sql, flags=re.I
        ):
            return False, "问题需要统计结果，SQL 必须包含 COUNT、SUM、AVG、MAX 或 MIN。"
        hidden_groups = _group_columns_missing_from_select(sql)
        if hidden_groups:
            return False, (
                "GROUP BY 字段必须出现在 SELECT 中，当前缺少: "
                + ", ".join(hidden_groups)
            )
        return True, "ok"

    def rewrite_sql(
        self, question: str, sql: str, error: str, table_names: list[str]
    ) -> str:
        prompt = f"""你是 SQLite 专家。下面的 SQL 不能正确回答用户问题，请改写。
只输出改写后的一个 ```sql 代码块，不要解释。
仍然只能是一条 SELECT 或 WITH 查询，并且必须回答原来的问题。
必须使用这些表：{", ".join(table_names)}
艺术家和曲目之间要经过 albums。销售额写为 SUM(invoice_items.UnitPrice * invoice_items.Quantity)。
列名有歧义时加上表前缀。GROUP BY 的字段要同时出现在 SELECT 中。
不要原样返回失败的 SQL。

{_sql_hint(question)}

用户问题：{question}

原 SQL：
{sql}

问题：
{error}

可用表：
{self.parser.schema_text(table_names)}

外键：
{self.parser.relation_text(table_names)}
"""
        return _extract_sql(self.llm(prompt, temperature=0.1))

    def execute_sql(self, sql: str) -> pd.DataFrame:
        return self.parser.execute_sql(sql)

    def summarize(self, question: str, sql: str, frame: pd.DataFrame) -> str:
        if frame.empty:
            preview = "查询结果为空"
        else:
            preview = frame.head(30).to_markdown(index=False)
        truncated = "结果已截断，仅展示前 30 行。" if len(frame) > 30 else ""
        prompt = f"""你是数据分析助手。根据问题和查询结果用中文回答。
回答要直接给出结论，保留关键数字，不要编造结果里没有的数字。
如果结果为空，就说明没有查到匹配数据。

用户问题：{question}

执行 SQL：
{sql}

查询结果：
{preview}
{truncated}
"""
        return self.llm(prompt, temperature=0.2).strip()

    def run(self, question: str) -> dict[str, Any]:
        """按固定阶段执行一次问答，并打印每一步。"""
        print("\n========== SQL Agent ==========")
        print(f"问题: {question}")

        print("\n[1] 数据库解析")
        print("表:", ", ".join(self.parser.table_names))
        print(f"外键: {len(self.parser.foreign_keys)} 条")

        print("\n[2] table 选择")
        selection = self.select_tables(question)
        print("选中:", ", ".join(selection["tables"]))
        print("扩展:", ", ".join(selection["expanded_tables"]))
        print("理由:", selection["reason"])

        print("\n[3] sql 生成")
        tables = selection["expanded_tables"]
        sql = self.generate_sql(question, tables)
        print(sql)
        sql, attempts = self.repair_sql(question, sql, tables)

        print("\n[6] sql 执行")
        frame = self.execute_sql(sql)
        if frame.empty:
            print("(空结果)")
        else:
            print(frame.to_string(index=False))
        if frame.attrs.get("truncated"):
            print("结果较多，仅保留前 100 行。")

        print("\n[7] 结果汇总")
        summary = self.summarize(question, sql, frame)
        print(summary)

        return {
            "question": question,
            "selected_tables": selection["tables"],
            "expanded_tables": tables,
            "selection_reason": selection["reason"],
            "sql": sql,
            "attempts": attempts,
            "frame": frame,
            "summary": summary,
        }

    def _canon_tables(self, names: list[str]) -> list[str]:
        lookup = {name.lower(): name for name in self.parser.table_names}
        chosen = []
        for name in names:
            key = str(name).strip().strip("`").lower()
            actual = lookup.get(key)
            if actual and actual not in chosen:
                chosen.append(actual)
        return chosen

    def repair_sql(
        self, question: str, sql: str, table_names: list[str]
    ) -> tuple[str, list[dict]]:
        """校验失败时改写，并再次校验最后一版 SQL。"""
        attempts = []
        valid = False
        message = ""
        for index in range(self.max_retry):
            ruled = _add_group_columns_to_select(sql)
            if ruled != sql:
                print("\n[5] sql 改写")
                print("规则改写：把 GROUP BY 字段补进 SELECT")
                print(ruled)
                sql = ruled
            print(f"\n[4] sql 校验（第 {index + 1} 次）")
            valid, message = self.check_answer_sql(question, sql, table_names)
            attempts.append({"sql": sql, "ok": valid, "message": message})
            print("通过" if valid else message)
            if valid:
                print("\n[5] sql 改写")
                print("校验已通过，无需改写")
                break
            if index == self.max_retry - 1:
                break
            print("\n[5] sql 改写")
            rewritten = self.rewrite_sql(question, sql, message, table_names)
            print(rewritten)
            if not rewritten:
                message = "模型没有返回可执行的 SQL"
                valid = False
                continue
            sql = rewritten
        if not valid:
            raise RuntimeError(f"SQL 校验失败，已达到重试上限: {message}")
        return sql, attempts

    def _required_tables(self, question: str) -> list[str]:
        return self._keyword_tables(question)

    def _keyword_tables(self, question: str) -> list[str]:
        aliases = {
            "客户": "customers",
            "顾客": "customers",
            "员工": "employees",
            "雇员": "employees",
            "客服": "employees",
            "发票": "invoices",
            "订单": "invoices",
            "销售额": "invoice_items",
            "销售": "invoice_items",
            "购买": "invoice_items",
            "艺术家": "artists",
            "歌手": "artists",
            "乐队": "artists",
            "专辑": "albums",
            "曲目": "tracks",
            "歌曲": "tracks",
            "流派": "genres",
            "播放列表": "playlists",
            "歌单": "playlists",
            "媒体类型": "media_types",
        }
        chosen = []
        for word, table_name in aliases.items():
            if word in question and table_name not in chosen:
                chosen.append(table_name)
        return chosen

    def _connect_tables(self, table_names: list[str]) -> list[str]:
        """沿外键补齐选中表之间缺失的中间表。"""
        selected = [name for name in table_names if name in self.parser.table_names]
        if len(selected) <= 1:
            return selected
        graph: dict[str, set[str]] = {name: set() for name in self.parser.table_names}
        for item in self.parser.foreign_keys:
            left = item["constrained_table"]
            right = item["referred_table"]
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

        connected = [selected[0]]
        for target in selected[1:]:
            for name in _shortest_path(graph, selected[0], target):
                if name not in connected:
                    connected.append(name)
            if target not in connected:
                connected.append(target)
        return connected


def _normalize_sql(sql: str) -> str:
    statement = _extract_sql(sql).strip()
    statement = statement.rstrip(";").strip()
    return statement


def _extract_sql(text_value: str) -> str:
    if not text_value:
        return ""
    fenced = re.findall(r"```sql\s*(.*?)```", text_value, flags=re.I | re.S)
    if not fenced:
        fenced = re.findall(r"```\s*(.*?)```", text_value, flags=re.S)
    candidate = fenced[0] if fenced else text_value
    match = re.search(r"(?is)\b(with|select)\b[\s\S]*", candidate)
    statement = match.group(0) if match else candidate
    return statement.strip().rstrip(";").strip()


def _is_safe_select(sql: str) -> tuple[bool, str]:
    if not sql:
        return False, "SQL 为空"
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    stripped = re.sub(r"--.*?$", " ", stripped, flags=re.M).strip()
    if ";" in stripped:
        return False, "不允许多条 SQL"
    head = stripped.lstrip("(").lower()
    if not (head.startswith("select") or head.startswith("with")):
        return False, "仅允许 SELECT / WITH 查询"
    if re.search(
        r"\b(insert|update|delete|drop|alter|create|attach|detach|replace|pragma|vacuum|reindex)\b",
        head,
    ):
        return False, "检测到非查询语句"
    return True, "ok"


def _parse_tables(raw: str) -> list[str]:
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    tables = payload.get("tables", [])
    if isinstance(tables, str):
        return [tables]
    return [str(item) for item in tables]


def _parse_reason(raw: str) -> str:
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return raw.strip()
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return raw.strip()
    return str(payload.get("reason", "")).strip()


DEMO_QUESTIONS = [
    "每个国家分别有多少位客户？按客户数从高到低排列。",
    "曲目数量最多的音乐流派是什么？",
    "销售额最高的前 5 位艺术家是谁？请给出销售额。",
]


def _mentioned_tables(raw: str, table_names: list[str]) -> list[str]:
    found = []
    for name in table_names:
        if re.search(rf"\b{re.escape(name)}\b", raw, flags=re.I) and name not in found:
            found.append(name)
    return found


def _shortest_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    if start == goal:
        return [start]
    queue = deque([start])
    previous = {start: None}
    while queue:
        node = queue.popleft()
        for nxt in graph.get(node, ()):
            if nxt in previous:
                continue
            previous[nxt] = node
            if nxt == goal:
                path = [goal]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                path.reverse()
                return path
            queue.append(nxt)
    return []


def _needs_aggregate(question: str) -> bool:
    markers = ("多少", "数量", "销售额", "总计", "平均", "最高", "最低", "排行", "前 ")
    return any(marker in question for marker in markers)


def _group_columns_missing_from_select(sql: str) -> list[str]:
    match = re.search(
        r"\bgroup\s+by\s+(.+?)(\border\s+by\b|\blimit\b|\bhaving\b|$)",
        sql,
        flags=re.I | re.S,
    )
    if not match:
        return []
    select_clause = re.split(r"\bfrom\b", sql, maxsplit=1, flags=re.I)[0]
    missing = []
    present = False
    for part in match.group(1).split(","):
        token = part.strip().strip('`"[]')
        if not token:
            continue
        column = token.split(".")[-1].split()[0]
        if not column:
            continue
        if re.search(rf"\b{re.escape(column)}\b", select_clause, flags=re.I):
            present = True
        else:
            missing.append(column)
    # 主键和名称一起分组时，SELECT 里保留名称即可。
    if present:
        return []
    return missing


def _add_group_columns_to_select(sql: str) -> str:
    missing = _group_columns_missing_from_select(sql)
    if not missing:
        return sql
    return re.sub(
        r"(?i)^(\s*select\s+)",
        lambda match: match.group(1) + ", ".join(missing) + ", ",
        sql,
        count=1,
    )


def _sql_hint(question: str) -> str:
    """给小模型一个与问题同型的 SQLite 写法，避免漏掉连接路径。"""
    if "国家" in question and "客户" in question:
        example = """SELECT customers.Country AS country, COUNT(*) AS customer_count
FROM customers
GROUP BY customers.Country
ORDER BY customer_count DESC"""
    elif "流派" in question and ("曲目" in question or "歌曲" in question):
        example = """SELECT genres.Name AS genre_name, COUNT(*) AS track_count
FROM tracks
JOIN genres ON tracks.GenreId = genres.GenreId
GROUP BY genres.GenreId, genres.Name
ORDER BY track_count DESC
LIMIT 1"""
    elif "艺术家" in question and "销售" in question:
        example = """SELECT artists.Name AS artist_name,
       ROUND(SUM(invoice_items.UnitPrice * invoice_items.Quantity), 2) AS revenue
FROM artists
JOIN albums ON albums.ArtistId = artists.ArtistId
JOIN tracks ON tracks.AlbumId = albums.AlbumId
JOIN invoice_items ON invoice_items.TrackId = tracks.TrackId
GROUP BY artists.ArtistId, artists.Name
ORDER BY revenue DESC
LIMIT 5"""
    else:
        return ""
    return "如果和用户问题一致，可以沿用下面的写法：\n" + example


def demonstrate_rewrite(agent: SQLAgent) -> None:
    """故意提交错误表名，展示校验失败后的改写。"""
    question = "每个国家分别有多少位客户？"
    bad_sql = "SELECT Country, COUNT(*) AS customer_count FROM customer GROUP BY Country"
    print("\n========== SQL 改写演示 ==========")
    print("问题:", question)
    print("错误 SQL:", bad_sql)
    sql, _ = agent.repair_sql(question, bad_sql, ["customers"])
    print(agent.execute_sql(sql).head(5).to_string(index=False))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        agent = SQLAgent(DEFAULT_DB)
        demonstrate_rewrite(agent)
        for question in DEMO_QUESTIONS:
            agent.run(question)
        return

    question = " ".join(sys.argv[1:]).strip() or DEMO_QUESTIONS[0]
    SQLAgent(DEFAULT_DB).run(question)


if __name__ == "__main__":
    main()
