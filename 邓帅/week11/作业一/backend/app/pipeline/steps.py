"""pipeline 各步骤实现:选表/生成/校验/改写/执行/汇总。

第 1 步「数据库解析」由 DBParser 承担(db_parser.py);
第 4 步「SQL 校验」由 validator.py 承担;
本模块负责 LLM 交互步骤与执行器,输入输出均为纯数据,便于测试。
"""
import json
import re
import sqlite3

from app.llm import chat
from app.pipeline import prompts
from app.validator import check_syntax, extract_sql, validate_select_only


# ---------- 第 2 步:table 选择 ----------

async def select_tables(question: str, parser) -> list[str]:
    """LLM 从业务表清单中挑出回答问题所需的表。"""
    tables_brief = "\n".join(
        f"{name}: {', '.join(parser.get_table_fields(name).index)}"
        for name in parser.business_tables
    )
    relations = parser.get_data_relations().to_markdown(index=False)
    reply = await chat(
        prompts.SELECT_TABLES_PROMPT.format(
            tables_brief=tables_brief, relations=relations, question=question
        )
    )
    return parse_tables_output(reply, parser.business_tables)


def parse_tables_output(text: str, valid_tables: list[str]) -> list[str]:
    """解析 LLM 选表输出:JSON 数组优先,失败则从文本抽合法表名,再去重过滤。

    全部非法时返回空列表,由上层(runner)兜底为全部业务表。
    """
    valid_set = set(valid_tables)
    text = (text or "").strip()

    # 优先:剥围栏后直接/嵌入解析 JSON(数组,或 dict 中首个数组值)
    body = re.sub(r"```(?:json)?", "", text).strip()
    candidates: list[str] = []
    for chunk in re.findall(r"[\[{].*[\]}]", body, re.DOTALL):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            candidates += [x for x in obj if isinstance(x, str)]
        elif isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    candidates += [x for x in v if isinstance(x, str)]
    if not candidates:
        # 兜底:文本中按词边界抽取合法表名
        candidates = [
            t for t in valid_tables if re.search(rf"\b{re.escape(t)}\b", text)
        ]

    # 去重(保持出现顺序) + 过滤非法
    seen: set[str] = set()
    ordered: list[str] = []
    for t in candidates:
        if t in valid_set and t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


# ---------- 第 3 步:SQL 生成 ----------

async def generate_sql(question: str, tables: list[str], parser) -> str:
    """LLM 依据选中表的 schema/样例/外键生成 SQL(extract_sql 清洗后返回)。"""
    tables_schema = []
    for name in tables:
        fields_df = parser.get_table_fields(name)
        lines = [
            f"{idx} ({row['type']}, 取值如: {', '.join(map(str, row['random']))})"
            for idx, row in fields_df.iterrows()
        ]
        tables_schema.append(f"表 {name}:\n" + "\n".join(lines))

    tables_sample = "\n\n".join(
        f"表 {name}:\n{parser.get_table_sample(name, n=3).to_markdown(index=False)}"
        for name in tables
    )
    relations = _relations_between(parser, tables)

    reply = await chat(
        prompts.GENERATE_SQL_PROMPT.format(
            question=question,
            tables_schema="\n\n".join(tables_schema),
            tables_sample=tables_sample,
            relations=relations,
        )
    )
    return extract_sql(reply)


# ---------- 第 4 步:SQL 校验(组合 validator 两道检查) ----------

def validate_sql(sql: str, conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    """白名单 + EXPLAIN 语法/表/列,两道检查合并。"""
    ok, errs = validate_select_only(sql)
    if not ok:
        return ok, errs
    return check_syntax(sql, conn)


# ---------- 第 5 步:SQL 改写 ----------

async def rewrite_sql(
    question: str, tables: list[str], sql: str, errors: list[str], parser
) -> str:
    """带错误反馈让 LLM 修正 SQL(extract_sql 清洗后返回)。"""
    reply = await chat(
        prompts.REWRITE_SQL_PROMPT.format(
            question=question,
            tables=", ".join(tables),
            sql=sql,
            errors="\n".join(f"- {e}" for e in errors),
        )
    )
    return extract_sql(reply)


# ---------- 第 6 步:SQL 执行 ----------

def execute_query(sql: str, conn: sqlite3.Connection, row_limit: int) -> dict:
    """只读连接上执行 SELECT,返回 {columns, rows, row_count, truncated};
    出错返回 {"error": ...} 由 runner 决定是否进入改写。
    """
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.Error as e:
        return {"error": f"SQL 执行失败: {e}"}


# ---------- 第 7 步:结果汇总 ----------

async def summarize(question: str, sql: str, result: dict, row_limit: int) -> str:
    """LLM 将查询结果组织为自然语言回答。"""
    lines = ["| " + " | ".join(map(str, result["columns"])) + " |"]
    for row in result["rows"]:
        lines.append("| " + " | ".join(map(str, row)) + " |")
    return await chat(
        prompts.SUMMARIZE_PROMPT.format(
            question=question,
            sql=sql,
            row_limit=row_limit,
            result="\n".join(lines),
        )
    )


# ---------- 内部工具 ----------

def _relations_between(parser, tables: list[str]) -> str:
    """过滤出与选中表相关的外键关系文本(全库兜底)。"""
    df = parser.get_data_relations()
    if len(df) == 0:
        return "(无外键信息)"
    tset = set(tables)
    related = df[
        df["constrained_table"].isin(tset) | df["referred_table"].isin(tset)
    ]
    if len(related) == 0:
        related = df
    return related.to_markdown(index=False)
