"""
流程（7 个步骤，逐步可观测）：
    1. 数据库解析   -> DBParser：读取表、字段、类型、主外键、统计信息、样例数据
    2. table 选择   -> LLM 根据问题从所有表中选出解答所需的最小表集合
    3. sql 生成     -> LLM 基于选中表的 schema + 外键 + 样例生成 SQLite SQL
    4. sql 校验     -> 程序侧静态校验（只读/语法 EXPLAIN 试跑）+ LLM 语义复核
    5. sql 改写     -> 校验不通过时，把错误信息回灌给 LLM 修复/优化 SQL（支持多轮）
    6. sql 执行     -> 在只读连接上真正执行 SQL，取回列名与数据行
    7. 结果汇总     -> LLM 把查询结果改写为自然语言回答

说明：
    - 大模型使用 DeepSeek（deepseek-flash），仅需在下方 DEEPSEEK_API_KEY 处填写 key；
    - 数据库解析用 Python 标准库 sqlite3 实现。
"""

import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

# Windows 控制台默认 GBK，无法编码的字符（如 emoji、对号）会抛 UnicodeEncodeError，
# 这里统一改为“无法编码则替换”，保证流程不因打印中断
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# ═══════════════════════════════════════════════════════════════════════════════
# 全局配置
# ═══════════════════════════════════════════════════════════════════════════════

#  DeepSeek API Key
DEEPSEEK_API_KEY = "your_api_key"

# DeepSeek 兼容 OpenAI 协议的接口地址
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 模型名称：DeepSeek 必须显式指定，可填 deepseek-flash / deepseek-chat / deepseek-reasoner
DEEPSEEK_MODEL = "deepseek-flash"

# 数据库文件路径（与 job1.py 同目录下的 chinook.db）
DB_PATH = str(Path(__file__).parent / "chinook.db")

# SQL 执行后最多返回并喂给 LLM 的行数，防止 prompt 过长
MAX_RESULT_ROWS = 20

# 单条 SQL 的“生成 -> 校验 -> 改写”最多迭代轮次
MAX_SQL_RETRIES = 3

# SQLite 内置的辅助表，不作为业务表参与解析
INTERNAL_TABLES = {"sqlite_sequence", "sqlite_stat1", "sqlite_temp_master", "sqlite_master"}

# 写入 prompt 时，单个字段值/样例值的最长展示长度，避免超长文本把 prompt 顶爆
MAX_CELL_CHARS = 60

# LLM 调用失败（网络抖动、超时、限流）时的重试次数
LLM_RETRY = 3


# ═══════════════════════════════════════════════════════════════════════════════
# 步骤 1：数据库解析
# ═══════════════════════════════════════════════════════════════════════════════

def quote_identifier(name: str) -> str:
    """把表名/列名包成 SQLite 引号标识符，并转义内部的双引号。

    避免表/列名带空格、关键字或引号时拼出的 SQL 语法错乱。
    """
    return '"' + str(name).replace('"', '""') + '"'


def short(value: Any, limit: int = MAX_CELL_CHARS) -> str:
    """把任意单元格值转为定长文本，防止 BLOB/长文本把 prompt 撑爆。"""
    if value is None:
        return "NULL"
    text = value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    return text if len(text) <= limit else text[:limit] + "..."


class DBParser:
    """SQLite 数据库解析器。

    负责把数据库的元信息（表 / 字段 / 类型 / 主键 / 外键 / 统计特征 / 样例数据）
    读出来并组织成文本，作为后续 LLM 选表、生成 SQL 的上下文；
    同时提供 SQL 的静态校验与执行能力。
    """

    def __init__(self, db_path: str) -> None:
        """初始化：连接数据库并解析所有业务表。

        :param db_path: sqlite 数据库文件路径
        """
        self.db_path = db_path

        # 以只读 URI 方式连接，避免 Agent 生成的 SQL 误改数据库
        # （Windows 路径需转正斜杠，否则会被 URI 解析吃掉转义字符）
        try:
            self.conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            # 兜底：若只读模式不可用（例如文件被锁定），退回普通连接
            self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()

        # 1) 获取所有表名，过滤掉 sqlite 内部表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        self.table_names = [r[0] for r in cursor.fetchall() if r[0] not in INTERNAL_TABLES]

        self._table_fields: dict[str, dict[str, dict[str, Any]]] = {}  # 每张表的字段及统计信息
        self._table_rows: dict[str, list[sqlite3.Row]] = {}            # 每张表的样例行
        self._row_counts: dict[str, int] = {}                          # 每张表的总行数
        self.foreign_keys: list[dict[str, Any]] = []                   # 全库外键关系

        print(f"[1] 数据库解析：共 {len(self.table_names)} 张业务表")
        for table in self.table_names:
            self._parse_table(cursor, table)
            print(f"    - {table}: {self._row_counts[table]} 行 / "
                  f"{len(self._table_fields[table])} 列")

    # -------------------- 内部解析逻辑 --------------------

    def _parse_table(self, cursor, table: str) -> None:
        """解析单张表：字段信息 + 统计特征 + 外键 + 样例数据。"""
        self._table_fields[table] = {}
        quoted = quote_identifier(table)

        # 字段元信息：cid, name, type, notnull, dflt_value, pk
        cursor.execute(f"PRAGMA table_info({quoted});")
        columns = cursor.fetchall()

        # 外键：id, seq, table(被引用表), from(本表列), to(被引用列), ...
        cursor.execute(f"PRAGMA foreign_key_list({quoted});")
        for fk in cursor.fetchall():
            self.foreign_keys.append({
                "constrained_table": table,
                "constrained_columns": [fk[3]],
                "referred_table": fk[2],
                "referred_columns": [fk[4]],
            })

        # 表行数
        cursor.execute(f"SELECT COUNT(*) FROM {quoted};")
        self._row_counts[table] = cursor.fetchone()[0]

        # 逐列统计：唯一值数、空值数、最大/最小值、抽样取值、众数
        for col in columns:
            col_name, col_type = col[1], (col[2] or "").upper()
            info = {
                "name": col_name,
                "type": col[2],
                "nullable": not col[3],
                "default": col[4],
                "primary_key": col[5],
            }

            col_quoted = quote_identifier(col_name)
            # 注意：COUNT(DISTINCT col) 与 COUNT(col) 都会忽略 NULL，
            # 因此空值数必须用 COUNT(*) - COUNT(col)，而不是 COUNT(*) - COUNT(DISTINCT col)
            cursor.execute(
                f'SELECT COUNT(DISTINCT {col_quoted}), COUNT({col_quoted}), COUNT(*), '
                f'MIN({col_quoted}), MAX({col_quoted}) FROM {quoted};'
            )
            distinct_cnt, non_null_cnt, total_cnt, min_value, max_value = cursor.fetchone()
            info["distinct"] = distinct_cnt
            info["nan_count"] = total_cnt - non_null_cnt
            info["min"] = min_value
            info["max"] = max_value

            # 抽样取值（去空），让 LLM 知道字段里真实存的是什么样子
            cursor.execute(
                f'SELECT DISTINCT {col_quoted} FROM {quoted} '
                f'WHERE {col_quoted} IS NOT NULL LIMIT 3;'
            )
            info["random"] = [short(r[0]) for r in cursor.fetchall()]

            # 文本类型列再统计一个高频值（众数），便于识别枚举型字段
            if "CHAR" in col_type or "TEXT" in col_type:
                cursor.execute(
                    f'SELECT {col_quoted}, COUNT(*) c FROM {quoted} '
                    f'WHERE {col_quoted} IS NOT NULL '
                    f'GROUP BY {col_quoted} ORDER BY c DESC LIMIT 1;'
                )
                row = cursor.fetchone()
                info["mode"] = row[0] if row else None

            self._table_fields[table][col_name] = info

        # 样例行（前 2 行），作为 few-shot 上下文
        cursor.execute(f'SELECT * FROM {quoted} LIMIT 2;')
        self._table_rows[table] = cursor.fetchall()

    # -------------------- 对外查询接口 --------------------

    def get_table_fields(self, table: str) -> dict[str, dict[str, Any]]:
        """获取指定表的字段信息与统计特征。"""
        return self._table_fields.get(table, {})

    def get_data_relations(self) -> list[dict[str, Any]]:
        """获取全库外键关系（表之间的连接方式）。"""
        return self.foreign_keys

    def get_table_sample(self, table: str) -> tuple[list[str], list[list[Any]]]:
        """获取指定表的样例行，返回 (列名, 行数据)。"""
        rows = self._table_rows.get(table, [])
        if not rows:
            return [], []
        return list(rows[0].keys()), [list(r) for r in rows]

    def describe_all_tables(self) -> str:
        """把所有表压缩成一段简介文本，供“选表”步骤使用。"""
        lines = []
        for table in self.table_names:
            cols = ", ".join(self._table_fields[table].keys())
            lines.append(f"- {table}（{self._row_counts[table]} 行）字段：{cols}")
        return "\n".join(lines)

    def describe_table(self, table: str) -> str:
        """把单张表的完整 schema + 统计 + 样例渲染为文本，供“生成 SQL”步骤使用。"""
        lines = [f"### 表 {table}（共 {self._row_counts.get(table, 0)} 行）"]
        for name, info in self._table_fields.get(table, {}).items():
            flags = []
            if info["primary_key"]:
                flags.append("PRIMARY KEY")
            if not info["nullable"]:
                flags.append("NOT NULL")
            lines.append(
                f"- {name} {info['type']} {' '.join(flags)}".rstrip()
                + f" | 唯一值:{info['distinct']} 空值:{info['nan_count']}"
                + f" 最小:{short(info['min'], 30)} 最大:{short(info['max'], 30)}"
                + f" 取值样例:{info['random']}"
            )

        cols, rows = self.get_table_sample(table)
        if rows:
            lines.append("样例数据：")
            lines.append(" | ".join(cols))
            lines.extend(" | ".join(short(v, 30) for v in r) for r in rows)
        return "\n".join(lines)

    def describe_relations(self, tables: list[str]) -> str:
        """筛选出与所选表相关的外键关系文本。"""
        selected = set(tables)
        lines = []
        for fk in self.foreign_keys:
            if not selected & {fk["constrained_table"], fk["referred_table"]}:
                continue
            cols = ", ".join(fk["constrained_columns"])
            # 未显式声明被引用列时，SQLite 默认引用主键
            referred = ", ".join(c for c in fk["referred_columns"] if c) or "主键"
            lines.append(f"- {fk['constrained_table']}.{cols} -> {fk['referred_table']}.{referred}")
        return "\n".join(lines) if lines else "（无外键关系）"

    # -------------------- SQL 校验与执行 --------------------

    @staticmethod
    def static_check(sql: str) -> tuple[bool, str]:
        """静态校验：只允许单条只读查询（SELECT / WITH），禁止 DML/DDL。"""
        cleaned = sql.strip().rstrip(";").strip()
        if not cleaned:
            return False, "SQL 为空"

        # 先去掉注释，再把字符串/标识符字面量置空，
        # 避免字面量里的分号、关键字被误判（也避免只有注释时取首词报错）
        no_comment = re.sub(r"--.*?$|/\*.*?\*/", " ", cleaned, flags=re.M | re.S)
        no_literal = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "?", no_comment)
        if ";" in no_literal:
            return False, "只允许执行单条查询，检测到多条语句（存在分号）"

        tokens = no_literal.split(None, 1)
        if not tokens:
            return False, "SQL 只有注释或空白，没有可执行语句"
        head = tokens[0].upper()
        if head not in {"SELECT", "WITH"}:
            return False, f"只允许只读查询，检测到语句类型：{head}"
        return True, "ok"

    def check_sql(self, sql: str) -> tuple[bool, str]:
        """SQL 校验：静态检查 + EXPLAIN 试跑（真正编译但不执行）。

        :return: (是否可用, 错误信息)
        """
        ok, msg = self.static_check(sql)
        if not ok:
            return False, msg
        try:
            self.conn.execute(f"EXPLAIN {sql}")
            return True, "ok"
        except sqlite3.Error as e:
            # 只保留异常摘要，方便直接回灌给 LLM 作为改写依据
            return False, f"SQL 编译失败：{type(e).__name__}: {e}"

    def execute_sql(self, sql: str) -> tuple[bool, str, list[str], list[list[Any]]]:
        """执行 SQL。

        :return: (是否成功, 错误信息, 列名列表, 数据行列表)
        """
        try:
            cursor = self.conn.execute(sql)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            # 多取一行用于判断结果是否被截断
            rows = [list(r) for r in cursor.fetchmany(MAX_RESULT_ROWS + 1)]
            return True, "ok", cols, rows
        except sqlite3.Error as e:
            return False, f"SQL 执行失败：{type(e).__name__}: {e}", [], []


# ═══════════════════════════════════════════════════════════════════════════════
# LLM 调用封装
# ═══════════════════════════════════════════════════════════════════════════════

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def llm_chat(system: str, user: str, json_mode: bool = False) -> str:
    """调用 DeepSeek，返回文本内容（失败时重试，避免一次网络抖动中断整个流程）。

    :param system: 系统提示（角色与输出要求）
    :param user: 用户提示（问题与上下文）
    :param json_mode: 是否开启 JSON Mode（DeepSeek 支持 json_object，不支持 json_schema）
    """
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_error = None
    for attempt in range(LLM_RETRY):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                **kwargs,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # 超时/限流/返回体异常等统一重试
            last_error = exc
            wait = 2 * (attempt + 1)
            print(f"    [WARN] 调用 DeepSeek 失败（第 {attempt + 1}/{LLM_RETRY} 次）：{exc}，{wait}s 后重试")
            time.sleep(wait)

    raise RuntimeError(f"连续 {LLM_RETRY} 次调用 DeepSeek 失败: {last_error}") from last_error


def parse_json(text: str) -> dict:
    """容错解析 LLM 返回的 JSON（自动剥离 ```json 代码块等噪声）。"""
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 再尝试从文本中截取第一个 {...}
        m = re.search(r"\{.*\}", cleaned, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


def extract_sql(text: str) -> str:
    """从 LLM 输出中提取纯 SQL 文本（去除 markdown 代码块与前后说明）。"""
    cleaned = text.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if m:
        cleaned = m.group(1).strip()
    cleaned = cleaned.strip("`").strip()
    # 去掉 "sql\n" 前缀
    if cleaned.lower().startswith("sql\n"):
        cleaned = cleaned[4:].strip()
    return cleaned.rstrip(";").strip()


def pick_sql(data: dict, raw: str) -> str:
    """从 LLM 返回中取出 SQL。

    JSON 解析成功时只取 sql 字段（字段为空就返回空串，不能把整段 JSON 当 SQL）；
    JSON 解析失败（模型直接输出了裸 SQL）时，才退化为从原始文本中提取。
    """
    if data:
        return extract_sql(str(data.get("sql", "") or ""))
    return extract_sql(raw)


def as_bool(value: Any, default: bool = True) -> bool:
    """把 LLM 返回的布尔字段归一化。

    模型可能输出 "false"/0/"否" 等，直接写 `value is False` 会把它们当成真，
    导致有问题的 SQL 被放行。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "是", "ok", "通过", "valid"}:
        return True
    if text in {"false", "0", "no", "n", "否", "不通过", "invalid"}:
        return False
    return default


def to_markdown(cols: list[str], rows: list[list[Any]], limit: int = MAX_RESULT_ROWS) -> str:
    """把查询结果渲染为简单表格文本（不依赖 tabulate）。"""
    show = rows[:limit]
    lines = [" | ".join(cols)]
    lines.extend(" | ".join(short(v, 40) for v in r) for r in show)
    if len(rows) > limit:
        lines.append(f"...（结果超过 {limit} 行，此处仅展示前 {limit} 行）")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════════════════

# 步骤 2：选表
TABLE_SELECT_PROMPT = """你是一个专业的数据库专家。现在有一个 SQLite 数据库，表清单如下：
{all_tables}

请根据用户问题，选出解答所需的最小表集合。注意：
- 如果问题需要跨表信息，必须把 JOIN 所涉及的表都选上；
- 只能使用上面列出的表名，不要臆造；
- 只输出 JSON，格式为：{{"tables": ["表名1", "表名2"], "reason": "一句话理由"}}

用户问题：{question}
"""

# 步骤 3：生成 SQL
SQL_GEN_PROMPT = """你是一个专业的数据库专家，请使用 SQLite 语法，根据下面的表结构生成查询 SQL。

【选中表及 schema】
{schema}

【表之间的外键关系】
{relations}

生成要求：
1. 只能写一条只读查询（SELECT 或 WITH ... SELECT），禁止 INSERT/UPDATE/DELETE/DROP/PRAGMA；
2. 表名、字段名必须与下面给出的 schema 完全一致，不要臆造（例如 invoices 上的国家字段是 BillingCountry，customers 上的才是 Country）；
3. 多表统计时严格按【外键关系】书写 JOIN 条件；
4. 尽量用有意义的别名，不要 SELECT *；
5. 不要使用 MySQL/Oracle 专有函数（如 DATE_FORMAT、NVL、IF），日期处理用 SQLite 的 strftime()/date()，空值兼容用 IFNULL/coalesce；
6. 统计“最多/最少/排名”类问题请加 ORDER BY 与 LIMIT；
7. 只输出 JSON，格式为：{{"sql": " select 语句", "explanation": "中文说明"}}

用户问题：{question}
"""

# 步骤 4：语义校验（程序校验之后的 LLM 复核）
SQL_VERIFY_PROMPT = """你是一个专业的数据库专家，请审查下面这条 SQLite SQL 能否正确回答用户问题。

【选中表及 schema】
{schema}

【外键关系】
{relations}

【用户问题】
{question}

【待审查 SQL】
{sql}

请重点检查：表名/字段名是否存在、JOIN 条件是否正确、聚合与分组是否匹配、
筛选条件（如国家、时间、类型取值）是否与 schema 中的真实取值一致、是否偏离问题。
只输出 JSON，格式为：{{"valid": true 或 false, "issues": "若不合格，说明具体问题；合格则填空字符串"}}
"""

# 步骤 5：根据问题/错误信息改写 SQL
SQL_REWRITE_PROMPT = """你是一个专业的数据库专家。上一步生成的 SQL 存在问题，请你改写修复。

【选中表及 schema】
{schema}

【外键关系】
{relations}

【用户问题】
{question}

【原 SQL】
{sql}

【问题描述 / 数据库报错】
{error}

改写要求：保持只读单条查询；使用 SQLite 语法；只修改有问题的部分。
只输出 JSON，格式为：{{"sql": "修复后的 SQL", "explanation": "说明改了什么"}}
"""

# 步骤 7：结果汇总为自然语言
SUMMARY_PROMPT = """你是一个专业的数据分析师，请结合用户问题、执行 SQL 和查询结果，
用简洁清晰的中文回答用户问题。要求：
- 直接给出结论，不要复述 SQL；
- 结果行数较多时做归纳（TopN、趋势、总量），不要逐行罗列；
- 如果结果为空，说明没有符合条件的数据。

【用户问题】
{question}

【执行 SQL】
{sql}

【查询结果】
{result}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Agent Workflow
# ═══════════════════════════════════════════════════════════════════════════════

class SqlAgent:
    """完整的 SQL Agent 工作流：解析 -> 选表 -> 生成 -> 校验 -> 改写 -> 执行 -> 汇总。"""

    def __init__(self, db_path: str) -> None:
        self.parser = DBParser(db_path)

    # -------------------- 步骤 2：table 选择 --------------------

    def select_tables(self, question: str) -> list[str]:
        """让 LLM 从数据库中挑选与问题相关的表，并做合法性过滤。"""
        prompt = TABLE_SELECT_PROMPT.format(all_tables=self.parser.describe_all_tables(),
                                            question=question)
        data = parse_json(llm_chat("你是数据库专家，只输出 JSON。", prompt, json_mode=True))

        # 兼容模型把 tables 写成字符串（如 "tracks, genres"）的情况
        raw_tables = data.get("tables", [])
        if isinstance(raw_tables, str):
            raw_tables = re.split(r"[,\s、]+", raw_tables)
        tables = [t.strip() for t in raw_tables if isinstance(t, str) and t.strip()]
        # 白名单过滤：剔除模型臆造出来的表名
        tables = [t for t in tables if t in self.parser.table_names]
        if not tables:
            # 兜底：选不出表时退化为只用前几张表（控制 prompt 长度），不让流程中断
            tables = self.parser.table_names[:5]
            print("    [WARN] 选表结果为空，回退为部分表")
        print(f"[2] table 选择：{tables}（理由：{data.get('reason', '未提供')}）")
        return tables

    # -------------------- 步骤 3：sql 生成 --------------------

    def generate_sql(self, question: str, schema: str, relations: str) -> str:
        """基于表结构生成 SQL。"""
        prompt = SQL_GEN_PROMPT.format(schema=schema, relations=relations, question=question)
        raw = llm_chat("你是数据库专家，只输出 JSON。", prompt, json_mode=True)
        sql = pick_sql(parse_json(raw), raw)
        print(f"[3] sql 生成：\n{sql}")
        return sql

    # -------------------- 步骤 4：sql 校验 --------------------

    def verify_sql(self, sql: str, question: str, schema: str, relations: str) -> tuple[bool, str]:
        """双重校验：先做程序侧静态检查 + EXPLAIN 试跑，再做 LLM 语义复核。"""
        # 4.1 程序校验（语法/只读）
        ok, msg = self.parser.check_sql(sql)
        if not ok:
            print(f"[4] sql 校验：[FAIL] 程序校验未通过 -> {msg}")
            return False, msg

        # 4.2 LLM 语义复核（结果不可解析时默认放行，避免流程死循环）
        prompt = SQL_VERIFY_PROMPT.format(schema=schema, relations=relations,
                                         question=question, sql=sql)
        data = parse_json(llm_chat("你是数据库专家，只输出 JSON。", prompt, json_mode=True))
        if not data:
            print("[4] sql 校验：[OK] 语法通过（LLM 复核结果不可解析，默认放行）")
            return True, "ok"

        if not as_bool(data.get("valid"), default=True):
            issues = data.get("issues") or "LLM 判定语义不正确"
            print(f"[4] sql 校验：[FAIL] 语义复核不通过 -> {issues}")
            return False, str(issues)

        print("[4] sql 校验：[OK] 语法与语义均通过")
        return True, "ok"

    # -------------------- 步骤 5：sql 改写 --------------------

    def rewrite_sql(self, sql: str, error: str, question: str, schema: str, relations: str) -> str:
        """把错误信息回灌给 LLM，让其改写/修复 SQL。"""
        prompt = SQL_REWRITE_PROMPT.format(schema=schema, relations=relations, question=question,
                                          sql=sql, error=error)
        raw = llm_chat("你是数据库专家，只输出 JSON。", prompt, json_mode=True)
        data = parse_json(raw)
        # 改写失败时保留原 SQL，交由下一轮重新生成
        new_sql = pick_sql(data, raw) or sql
        print(f"[5] sql 改写：\n{new_sql}")
        return new_sql

    # -------------------- 步骤 6：sql 执行 --------------------

    def execute(self, sql: str) -> tuple[bool, str, str]:
        """执行 SQL，返回 (是否成功, 结果表格文本, 错误信息)。"""
        ok, err, cols, rows = self.parser.execute_sql(sql)
        if not ok:
            print(f"[6] sql 执行：[FAIL] 失败 -> {err}")
            return False, "", err
        shown = rows[:MAX_RESULT_ROWS]
        result = to_markdown(cols, rows) if cols else "（无结果集）"
        suffix = f"（结果超过 {MAX_RESULT_ROWS} 行，仅展示部分）" if len(rows) > MAX_RESULT_ROWS else ""
        print(f"[6] sql 执行：[OK] 取回 {len(shown)} 行{suffix}")
        print("    " + "\n    ".join(result.splitlines()))
        return True, result, ""

    # -------------------- 步骤 7：结果汇总 --------------------

    def summarize(self, question: str, sql: str, result: str) -> str:
        """将查询结果改写为自然语言答案。"""
        prompt = SUMMARY_PROMPT.format(question=question, sql=sql, result=result)
        answer = llm_chat("你是资深数据分析师，擅长把数据结论讲清楚。", prompt)
        print(f"[7] 结果汇总：\n    {answer}")
        return answer

    # -------------------- 主流程 --------------------

    def run(self, question: str) -> dict:
        """执行完整 workflow，返回各步骤的中间产物，方便调试与提交截屏。"""
        print("=" * 78)
        print(f"用户问题：{question}")
        print("=" * 78)

        # 步骤 2：选表
        tables = self.select_tables(question)
        schema = "\n\n".join(self.parser.describe_table(t) for t in tables)
        relations = self.parser.describe_relations(tables)

        sql, final_answer, result_text = "", "", ""
        final_sql = ""          # 记录最近一次非空 SQL，用于失败时向用户展示
        tried: set[str] = set()  # 已经校验过的 SQL，避免同一句话被反复校验、白耗 API 调用
        # 步骤 3~6：生成 -> 校验 -> 改写 -> 执行，失败则带着报错最多重试 MAX_SQL_RETRIES 轮
        for attempt in range(1, MAX_SQL_RETRIES + 1):
            print(f"\n----- 第 {attempt} 轮 -----")
            if not sql:
                sql = self.generate_sql(question, schema, relations)
                if not sql:
                    print("    [WARN] LLM 未返回有效 SQL，重试")
                    continue
            if sql in tried:
                print("    [WARN] 生成了重复的 SQL，跳过校验重新生成")
                sql = ""
                continue
            tried.add(sql)
            final_sql = sql

            ok, msg = self.verify_sql(sql, question, schema, relations)
            if not ok:
                new_sql = self.rewrite_sql(sql, msg, question, schema, relations)
                if new_sql == sql:
                    # 改写没变化，再校验一次也是同样结果，直接下一轮重新生成
                    sql = ""
                    continue
                sql = new_sql
                final_sql = sql
                # 改写后再校验一次；仍不通过则进入下一轮重新生成
                ok, msg = self.verify_sql(sql, question, schema, relations)
                if not ok:
                    sql = "" if attempt < MAX_SQL_RETRIES else sql
                    continue

            # 校验通过 -> 执行
            exec_ok, result_text, err = self.execute(sql)
            if not exec_ok:
                sql = self.rewrite_sql(sql, err, question, schema, relations)
                continue
            break

        # 步骤 7：结果汇总（执行失败也给出说明性回答）
        if result_text:
            final_answer = self.summarize(question, sql, result_text)
        else:
            final_answer = f"很抱歉，未能成功执行查询（最终 SQL：{final_sql or '无'}），无法回答问题。"
            print(f"[7] 结果汇总：{final_answer}")

        return {
            "question": question,
            "tables": tables,
            "sql": sql or final_sql,
            "result": result_text,
            "answer": final_answer,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 测试用例：验证 7 步流程是否跑通（填好 DEEPSEEK_API_KEY 后直接运行本文件）
    agent = SqlAgent(DB_PATH)

    # 未填 key 时，先把步骤 1 的解析结果打印出来，便于确认数据库连接正常
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "your_api_key":
        print("\n[提示] 尚未填写 DEEPSEEK_API_KEY，以下仅展示步骤 1（数据库解析）结果；"
              "在文件顶部填入 key 后重新运行即可跑通完整流程。")
        print(agent.parser.describe_all_tables())
        print("\n外键关系：")
        print(agent.parser.describe_relations(agent.parser.table_names))
        raise SystemExit(0)

    # 问题 1：需 tracks + genres 两张表 JOIN + 聚合排序
    question = "每种音乐类型（genre）下有多少首曲目？请按数量从多到少给出前 5 名。"

    # 问题 2（可替换）：只靠 invoices 的 BillingCountry 就能回答，用于验证单表场景
    # question = "哪个国家（BillingCountry）的订单总金额最高？给出国家和总金额。"

    # 问题 3（可替换）：需要 playlists + playlist_track + tracks + genres 四表链路，用于验证选表与 JOIN
    # question = "播放列表 'Music' 里包含哪些曲目？列出前 10 首的曲名和它们的音乐类型。"

    outputs = agent.run(question)

    print("\n" + "=" * 78)
    print("最终返回结构：")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
