"""DBParser 测试:基于 chinook.db 的元数据解析(离线,不依赖 LLM)。"""
import pytest

from app.config import DB_URL
from app.db_parser import DBParser

# 模块级共享实例:__init__ 逐表逐字段统计,只跑一次
@pytest.fixture(scope="module")
def parser() -> DBParser:
    return DBParser(DB_URL)


def test_table_names_contains_business_tables(parser):
    """sqlalchemy inspector 自动滤除 sqlite_ 前缀内部表,恰为 11 张业务表。"""
    assert len(parser.table_names) == 11
    for t in ["albums", "customers", "invoices", "tracks"]:
        assert t in parser.table_names


def test_business_tables_excludes_internal(parser):
    """业务表清单防御性排除内部表(换数据源也不引入 sqlite 内部表)。"""
    assert "sqlite_sequence" not in parser.business_tables
    assert "sqlite_stat1" not in parser.business_tables
    assert len(parser.business_tables) == len(parser.table_names)


def test_get_table_fields(parser):
    """字段信息含类型与统计列(distinct 等)。"""
    df = parser.get_table_fields("albums")
    assert list(df.columns)[:3] == ["name", "type", "nullable"]
    assert {"AlbumId", "Title", "ArtistId"} <= set(df.index)
    assert df.loc["Title", "distinct"] > 0


def test_get_data_relations(parser):
    """外键关系非空,且包含 invoices → customers。"""
    df = parser.get_data_relations()
    assert len(df) > 0
    pairs = set(
        zip(df["constrained_table"], df["referred_table"])
    )
    assert ("invoices", "customers") in pairs
    assert ("albums", "artists") in pairs


def test_get_table_sample(parser):
    """表样例返回 n 行 DataFrame,列与表字段一致。"""
    df = parser.get_table_sample("albums", n=3)
    assert len(df) == 3
    assert set(df.columns) == {"AlbumId", "Title", "ArtistId"}


def test_unknown_table_raises(parser):
    """未知表名应报错而非静默返回空。"""
    with pytest.raises(KeyError):
        parser.get_table_fields("no_such_table")
