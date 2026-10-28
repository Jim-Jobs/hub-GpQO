import pytest

from sql_agent.config import PROJECT_ROOT
from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutionError, SQLExecutor
from sql_agent.db.validator import SQLValidator, normalize_sql

# 用绝对路径，测试结果不随 cwd 变化（从仓库任意层级跑 pytest 都成立）。
CHINOOK_DB = PROJECT_ROOT / "ref" / "chinook.db"


@pytest.fixture(scope="module")
def catalog() -> SchemaCatalog:
    return SchemaCatalog(CHINOOK_DB)


def test_catalog_excludes_sqlite_internal_tables(catalog: SchemaCatalog) -> None:
    assert len(catalog.table_names) == 11
    assert "tracks" in catalog.table_names
    assert "sqlite_sequence" not in catalog.table_names
    assert "sqlite_stat1" not in catalog.table_names


def test_catalog_has_multiple_sample_rows(catalog: SchemaCatalog) -> None:
    profile = catalog._tables["albums"]
    assert profile.row_count == 347
    assert len(profile.sample_rows) == 3


def test_catalog_renders_join_edges(catalog: SchemaCatalog) -> None:
    edges = [edge.render() for edge in catalog.join_edges(["invoice_items", "invoices"])]
    assert "invoice_items.InvoiceId -> invoices.InvoiceId" in edges
    assert catalog.is_connected(["invoice_items", "invoices", "customers"])
    assert not catalog.is_connected(["albums", "customers"])


@pytest.mark.parametrize(
    ("sql", "ok"),
    [
        ("SELECT COUNT(DISTINCT ArtistId) FROM albums;", True),
        ("WITH x AS (SELECT 1 AS n) SELECT * FROM x", True),
        ("SELECT 'DROP' AS word", True),
        # REPLACE 是合法字符串函数，不得误杀（回归：曾被当写关键字拦截）。
        ("SELECT REPLACE(Name, 'a', 'b') FROM tracks LIMIT 1", True),
        ("REPLACE INTO albums (AlbumId) VALUES (1)", False),
        # 分号在末尾注释之前不算多语句。
        ("SELECT 1; -- 结尾注释带分号", True),
        ("DROP TABLE tracks", False),
        ("SELECT 1; DROP TABLE tracks", False),
        ("SELECT 1; SELECT 2", False),
        ("SELECT nope FROM tracks", False),
        ("", False),
    ],
)
def test_validator(sql: str, ok: bool) -> None:
    validator = SQLValidator(CHINOOK_DB)
    result = validator.validate(sql)
    assert result.ok is ok
    if ok:
        assert not result.normalized_sql.endswith(";")
    else:
        assert result.code is not None


def test_validator_does_not_execute() -> None:
    validator = SQLValidator(CHINOOK_DB)
    result = validator.validate("CREATE TABLE should_not_exist(x int)")
    assert not result.ok
    assert result.code.value == "unsafe"


def test_normalize_sql_removes_fence_and_semicolon() -> None:
    assert normalize_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


def test_executor_returns_rows_and_truncates() -> None:
    executor = SQLExecutor(CHINOOK_DB, max_rows=5)
    result = executor.execute("SELECT TrackId, Name FROM tracks ORDER BY TrackId")
    assert result.columns == ["TrackId", "Name"]
    # row_count 是真实总行数，rows 才是被截断后的行。
    assert result.row_count == 3503
    assert result.truncated is True
    assert len(result.rows) == 5
    assert result.rows[0][1] == "For Those About To Rock (We Salute You)"


def test_executor_row_count_is_exact_at_boundary() -> None:
    executor = SQLExecutor(CHINOOK_DB, max_rows=5)
    result = executor.execute("SELECT TrackId FROM tracks ORDER BY TrackId LIMIT 5")
    assert result.row_count == 5
    assert result.truncated is False


def test_executor_row_count_handles_cte() -> None:
    executor = SQLExecutor(CHINOOK_DB, max_rows=2)
    result = executor.execute(
        "WITH big AS (SELECT TrackId FROM tracks) SELECT TrackId FROM big"
    )
    assert result.row_count == 3503
    assert result.truncated is True
    assert len(result.rows) == 2


def test_executor_is_readonly() -> None:
    executor = SQLExecutor(CHINOOK_DB)
    with pytest.raises(SQLExecutionError):
        executor.execute("CREATE TABLE should_not_exist(x int)")
