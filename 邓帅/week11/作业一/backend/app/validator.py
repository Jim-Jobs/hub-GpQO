"""SQL 校验器:LLM 输出清洗、仅 SELECT 白名单、EXPLAIN 语法/表/列检查。

对应 workflow 第 4 步「SQL 校验」。校验产出的错误信息会作为反馈
喂给第 5 步「SQL 改写」的 prompt,因此报错要具体可定位。
"""
import re
import sqlite3

# 仅允许查询语句:首关键词必须是 SELECT 或 WITH(CTE);
# INSERT/UPDATE/DROP/ATTACH 等写操作语句作为首关键词即在此被拦
_ALLOWED_START = re.compile(r"^(with|select)\b", re.IGNORECASE)

# 语句中间的写操作:sqlite 的 WITH 可后接 INSERT/UPDATE/DELETE(CTE 写),
# 需全局拦截这三个 DML 关键词。其余写语句词(DROP/CREATE/ATTACH...)只能
# 作为顶层首关键词出现,已由 _ALLOWED_START 拦截,不再全局搜——避免误伤
# SELECT 中合法的 REPLACE() 等函数名
_FORBIDDEN_DML = re.compile(r"\b(insert|update|delete)\b", re.IGNORECASE)

# ```sql ... ``` 代码围栏
_FENCE = re.compile(r"```(?:sql)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

# 行注释与块注释(白名单判定前剥离)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def extract_sql(llm_output: str) -> str:
    """从 LLM 输出提取 SQL。

    优先取 ```sql 围栏内文本,否则用原文;去围栏/反引号;
    截断到第一条语句,防止夹带第二条写操作。
    """
    text = (llm_output or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    text = text.strip("`").strip()
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    return text


def _strip_comments(sql: str) -> str:
    """剥离 -- 行注释与 /* */ 块注释(仅用于白名单判定)。"""
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return sql.strip()


def validate_select_only(sql: str) -> tuple[bool, list[str]]:
    """安全白名单:仅允许 SELECT / WITH 开头的只读查询。

    返回 (是否通过, 错误列表)。
    """
    if not sql or not sql.strip():
        return False, ["SQL 为空"]

    stripped = _strip_comments(sql)
    errors: list[str] = []

    if not _ALLOWED_START.match(stripped):
        first_word = stripped.split(None, 1)[0] if stripped.split() else "(空)"
        errors.append(
            f"仅允许 SELECT 查询,当前首关键词为 {first_word.upper()},"
            f"不是 SELECT/WITH: {stripped[:40]!r}"
        )
    m = _FORBIDDEN_DML.search(stripped)
    if m:
        errors.append(f"包含禁止的写操作关键词: {m.group(1).upper()}")

    return (not errors), errors


def check_syntax(sql: str, conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    """语法/表/列检查:EXPLAIN 走 sqlite prepare 阶段(不真正执行),
    语法错误、表不存在、列不存在都会在此报出,且错误信息具体。

    conn: 只读连接(mode=ro),双保险防止执行写操作。
    """
    try:
        conn.execute(f"EXPLAIN {sql}")
        return True, []
    except sqlite3.Error as e:
        return False, [f"SQL 无法通过语法检查: {e}"]
