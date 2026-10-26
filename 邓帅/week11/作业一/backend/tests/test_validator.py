"""SQL 校验器测试:extract_sql 清洗、仅 SELECT 白名单、EXPLAIN 语法/表/列检查(离线)。"""
import sqlite3

import pytest

from app.config import DB_PATH
from app.validator import check_syntax, extract_sql, validate_select_only


@pytest.fixture(scope="module")
def ro_conn() -> sqlite3.Connection:
    """只读连接,EXPLAIN 检查用。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    yield conn
    conn.close()


# ---------- extract_sql:LLM 输出清洗 ----------

def test_extract_from_markdown_fence():
    text = "好的,这是SQL:\n```sql\nSELECT * FROM albums;\n```\n希望有帮助"
    assert extract_sql(text) == "SELECT * FROM albums"


def test_extract_from_bare_sql():
    assert extract_sql("SELECT 1;") == "SELECT 1"


def test_extract_stops_at_first_statement():
    """多语句只取第一条(防夹带)。"""
    sql = extract_sql("SELECT * FROM albums; DROP TABLE albums;")
    assert sql == "SELECT * FROM albums"


def test_extract_plain_fence():
    text = "```\nSELECT Title FROM albums\n```"
    assert extract_sql(text) == "SELECT Title FROM albums"


# ---------- validate_select_only:安全白名单 ----------

def test_select_allowed():
    ok, errs = validate_select_only("SELECT * FROM albums")
    assert ok and errs == []


def test_with_cte_allowed():
    """CTE(WITH ... SELECT)是合法查询形态。"""
    ok, _ = validate_select_only(
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t"
    )
    assert ok


def test_update_rejected():
    ok, errs = validate_select_only("UPDATE albums SET Title = 'x'")
    assert not ok and any("UPDATE" in e for e in errs)


def test_delete_rejected():
    ok, errs = validate_select_only("DELETE FROM albums")
    assert not ok


def test_drop_rejected():
    ok, errs = validate_select_only("DROP TABLE albums")
    assert not ok


def test_attach_rejected():
    """ATTACH/PRAGMA 等_sqlite_暗通道也要拦。"""
    ok, _ = validate_select_only("ATTACH DATABASE '/tmp/x' AS x")
    assert not ok


def test_leading_comment_ignored():
    """前导注释不影响首关键词判定。"""
    ok, _ = validate_select_only("-- 查询专辑\nSELECT * FROM albums")
    assert ok


def test_replace_function_allowed():
    """SELECT 中合法的 REPLACE() 字符串函数不得被写操作黑名单误伤。"""
    ok, errs = validate_select_only(
        "SELECT REPLACE(Title, 'old', 'new') FROM albums"
    )
    assert ok and errs == []


def test_with_cte_write_rejected():
    """sqlite 的 WITH 可后接 DELETE(CTE 写),语句中间的 DML 必须拦截。"""
    ok, errs = validate_select_only(
        "WITH t AS (SELECT 1) DELETE FROM albums"
    )
    assert not ok and any("DELETE" in e for e in errs)


# ---------- check_syntax:EXPLAIN 语法+表+列 ----------

def test_valid_sql_passes(ro_conn):
    ok, errs = check_syntax(
        "SELECT c.Country, SUM(i.Total) FROM invoices i "
        "JOIN customers c ON i.CustomerId = c.CustomerId "
        "GROUP BY c.Country",
        ro_conn,
    )
    assert ok and errs == []


def test_no_such_table(ro_conn):
    ok, errs = check_syntax("SELECT * FROM no_such_table", ro_conn)
    assert not ok and any("no_such_table" in e for e in errs)


def test_no_such_column(ro_conn):
    ok, errs = check_syntax("SELECT bad_col FROM albums", ro_conn)
    assert not ok and any("bad_col" in e for e in errs)


def test_syntax_error(ro_conn):
    ok, errs = check_syntax("SELECT FROM WHERE", ro_conn)
    assert not ok and errs
