"""DBParser 单元测试（离线，无需 LLM）。"""

import pandas as pd
import pytest

from sql_agent.db_parser import DBParser


def test_table_names(parser: DBParser):
    """应解析出 Chinook 的 11 张业务表（过滤 sqlite 内部表）。"""
    expected = {"albums", "artists", "customers", "employees", "genres",
                "invoices", "invoice_items", "media_types", "playlists",
                "playlist_track", "tracks"}
    assert expected == set(parser.table_names)


def test_foreign_keys(parser: DBParser):
    """应解析出外键关系，且包含 albums -> artists。"""
    relations = parser.get_data_relations()
    assert not relations.empty
    pair = {(r.constrained_table, r.referred_table) for r in relations.itertuples()}
    assert ("albums", "artists") in pair
    assert ("tracks", "albums") in pair


def test_get_table_fields(parser: DBParser):
    """albums 表应包含 Title / ArtistId 字段且 ArtistId 为外键字段。"""
    fields = parser.get_table_fields("albums")
    assert isinstance(fields, pd.DataFrame)
    cols = set(fields["column"])
    assert {"AlbumId", "Title", "ArtistId"} <= cols
    assert set(fields.columns) == {"column", "type", "nullable", "primary_key"}


def test_get_table_sample(parser: DBParser):
    """样例数据应返回指定行数的 DataFrame。"""
    sample = parser.get_table_sample("genres", n=3)
    assert len(sample) == 3
    assert "Name" in sample.columns


def test_schema_markdown(parser: DBParser):
    """schema_markdown 应包含表名、字段名和外键关系描述。"""
    md = parser.schema_markdown(["albums", "artists"])
    assert "## 表 albums" in md
    assert "## 表 artists" in md
    assert "ArtistId" in md
    assert "albums.ArtistId -> artists.ArtistId" in md
    assert "样例数据" in md


def test_schema_markdown_row_count(parser: DBParser):
    """schema 上下文应包含表的行数量级（帮助 LLM 感知数据规模）。"""
    md = parser.schema_markdown(["albums"])
    assert "约 347 行" in md  # chinook albums 表固定 347 行


def test_schema_markdown_cached(parser: DBParser):
    """schema 片段应被缓存：重复调用结果一致且缓存已填充。"""
    md1 = parser.schema_markdown(["albums", "artists"])
    md2 = parser.schema_markdown(["albums", "artists"])
    assert md1 == md2
    assert ("albums", 3) in parser._schema_md_cache
    assert ("artists", 3) in parser._schema_md_cache


def test_truncate_cell():
    """超长样例值应被截断，节省上下文 token。"""
    from sql_agent.db_parser import DBParser as P
    assert P._truncate_cell("x" * 100) == "x" * 60 + "…"
    assert P._truncate_cell("short") == "short"
    assert P._truncate_cell(None) is None


def test_execute_timeout(parser: DBParser, monkeypatch):
    """低效大笛卡尔积 SQL 应被语句级超时中止，而非无限执行。"""
    import time

    from sql_agent import config as cfg

    monkeypatch.setattr(cfg, "QUERY_TIMEOUT", 1.0)
    t0 = time.time()
    with pytest.raises(Exception):
        parser.execute_sql("SELECT COUNT(*) FROM tracks a, tracks b, tracks c")
    elapsed = time.time() - t0
    assert elapsed < 10, f"超时未生效，耗时 {elapsed:.1f}s"


def test_sqlite_readonly_engine(parser: DBParser):
    """SQLite 连接应为只读模式：直接写库必须失败（纵深防御）。"""
    from sqlalchemy import text

    with pytest.raises(Exception):
        with parser.engine.connect() as conn:
            conn.execute(text("INSERT INTO genres (Name) VALUES ('x')"))


def test_md_table_formatting(parser: DBParser):
    """Markdown 渲染（不依赖 pandas）：浮点尾差被规整，管道符被转义。"""
    md = DBParser._md_table(
        ["name", "total"],
        [("U2", 105.92999999999999), ("A|B", 12.0), ("C", None)],
    )
    assert "105.93" in md
    assert "105.929999" not in md
    assert "A\\|B" in md          # 内容中的 | 不破坏表格结构
    assert md.count("\n") == 4    # 表头 + 分隔 + 3 行
