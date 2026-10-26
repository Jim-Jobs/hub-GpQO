"""数据库结构解析、只读连接、SQL 校验和执行。"""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import SQLAlchemyError


def create_database_engine(db_path: str) -> Engine:
    """检查数据库文件是否存在，并创建 SQLite 只读数据库引擎。

    Args:
        db_path: 已有 SQLite 数据库的路径。

    Returns:
        数据库引擎，由调用者负责 dispose。
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{path}")
    url = URL.create(
        "sqlite",
        database=path.as_uri(),
        query={"mode": "ro", "uri": "true"},
    )
    return create_engine(url)


def parse_database(engine: Engine) -> dict[str, Any]:
    """读取所有普通表的字段、主键和外键，返回以表名为键的字典。"""
    inspector = inspect(engine)
    schema = {}
    for name in inspector.get_table_names():
        schema[name] = {
            "columns": [
                {
                    "name": column["name"],
                    "type": str(column["type"]),
                    "nullable": column["nullable"],
                }
                for column in inspector.get_columns(name)
            ],
            "primary_key": inspector.get_pk_constraint(name)["constrained_columns"],
            "foreign_keys": [
                {
                    "columns": key["constrained_columns"],
                    "referred_table": key["referred_table"],
                    "referred_columns": key["referred_columns"],
                }
                for key in inspector.get_foreign_keys(name)
            ],
        }
    return schema


def validate_sql(engine: Engine, sql: str) -> tuple[bool, str]:
    """检查 SELECT 入口并编译查询计划，返回是否通过及错误信息。

    此检查不执行原查询，也不能证明查询正确理解了用户问题。
    单语句限制由 SQLite 驱动执行，写入限制由只读连接提供。
    """
    words = sql.strip().split(maxsplit=1)
    if not words or words[0].upper() != "SELECT":
        return False, "学习版只支持以 SELECT 开头的单条查询，不支持 WITH。"
    try:
        with engine.connect() as connection:
            connection.execute(text("EXPLAIN QUERY PLAN " + sql)).all()
        return True, ""
    except SQLAlchemyError as exc:
        return False, str(exc)


def execute_sql(engine: Engine, sql: str, max_rows: int = 100) -> dict[str, Any]:
    """执行查询，返回列名、数据行以及是否截断的标记。

    Args:
        engine: 只读数据库引擎。
        sql: 已通过校验的查询。
        max_rows: 向汇总模型提供的最大行数，不限制数据库计算量。
    """
    if max_rows < 1:
        raise ValueError("max_rows 必须大于零。")
    with engine.connect() as connection:
        result = connection.execute(text(sql))
        columns = list(result.keys())
        rows = result.fetchmany(max_rows + 1)
        return {
            "columns": columns,
            "rows": [list(row) for row in rows[:max_rows]],
            "truncated": len(rows) > max_rows,
        }
