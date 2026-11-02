"""SQL 安全校验测试（只读白名单）。"""

import pytest

from sql_agent.db_parser import DBParser, SQLSafetyError


class TestCheckSql:
    def test_valid_select(self, parser: DBParser):
        ok, _ = DBParser.check_sql("SELECT * FROM tracks LIMIT 5")
        assert ok

    def test_valid_select_with_semicolon(self, parser: DBParser):
        ok, _ = DBParser.check_sql("SELECT COUNT(*) FROM albums;")
        assert ok

    def test_valid_with_cte(self, parser: DBParser):
        sql = ("WITH t AS (SELECT GenreId, COUNT(*) c FROM tracks GROUP BY GenreId) "
               "SELECT * FROM t ORDER BY c DESC")
        ok, _ = DBParser.check_sql(sql)
        assert ok

    def test_valid_with_comment(self, parser: DBParser):
        ok, _ = DBParser.check_sql("-- 注释\nSELECT 1")
        assert ok

    def test_empty(self, parser: DBParser):
        ok, msg = DBParser.check_sql("   ")
        assert not ok

    def test_insert_forbidden(self, parser: DBParser):
        ok, msg = DBParser.check_sql("INSERT INTO genres (Name) VALUES ('x')")
        assert not ok and "INSERT" in msg

    @pytest.mark.parametrize("sql", [
        "UPDATE tracks SET Name = 'x'",
        "DELETE FROM tracks",
        "DROP TABLE tracks",
        "ALTER TABLE tracks ADD COLUMN x INT",
        "CREATE TABLE t (id INT)",
        "PRAGMA table_info(tracks)",
        "ATTACH DATABASE 'a.db' AS a",
        "VACUUM",
    ])
    def test_write_operations_forbidden(self, parser: DBParser, sql: str):
        ok, _ = DBParser.check_sql(sql)
        assert not ok

    def test_multi_statement_forbidden(self, parser: DBParser):
        ok, msg = DBParser.check_sql("SELECT 1; DROP TABLE tracks")
        assert not ok

    def test_comment_stripped_safely(self, parser: DBParser):
        """注释会被安全剥离：注释中的写关键词不会被执行，应放行。"""
        ok, _ = DBParser.check_sql("SELECT 1 -- DELETE FROM tracks")
        assert ok

    def test_write_keyword_outside_comment(self, parser: DBParser):
        """正文中的写关键词必须被拦截。"""
        ok, _ = DBParser.check_sql("SELECT 1 WHERE 1=1; DELETE FROM tracks")
        assert not ok

    def test_string_literal_not_blocked(self, parser: DBParser):
        """字符串字面量中的写关键词是合法数据，不应误杀。"""
        ok, _ = DBParser.check_sql("SELECT * FROM tracks WHERE Composer = 'DELETE'")
        assert ok

    def test_string_literal_execute(self, parser: DBParser):
        """含敏感字面量的合法查询可正常执行。"""
        columns, rows, _ = parser.execute_sql(
            "SELECT COUNT(*) AS n FROM tracks WHERE Composer = 'DELETE'")
        assert columns == ["n"] and rows[0][0] == 0


class TestExecuteSql:
    def test_execute_success(self, parser: DBParser):
        columns, rows, truncated = parser.execute_sql(
            "SELECT Name FROM genres ORDER BY Name LIMIT 3")
        assert columns == ["Name"]
        assert len(rows) == 3
        assert not truncated

    def test_execute_row_limit(self, parser: DBParser):
        """结果超过 max_rows 应被截断。"""
        columns, rows, truncated = parser.execute_sql(
            "SELECT TrackId FROM tracks", max_rows=10)
        assert len(rows) == 10
        assert truncated

    def test_execute_safety_error(self, parser: DBParser):
        with pytest.raises(SQLSafetyError):
            parser.execute_sql("DELETE FROM tracks")

    def test_execute_sql_error(self, parser: DBParser):
        """不存在的字段应抛出执行异常（交由 Agent 重试回路处理）。"""
        with pytest.raises(Exception):
            parser.execute_sql("SELECT not_exist_field FROM tracks")

    def test_result_to_markdown(self, parser: DBParser):
        md = parser.result_to_markdown("SELECT COUNT(*) AS n FROM tracks")
        assert "n" in md
        assert "3503" in md  # chinook tracks 表固定 3503 行
