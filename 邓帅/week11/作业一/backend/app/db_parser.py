"""数据库解析(DBParser):复用课件 demo 的 sqlalchemy 实现,适配 2.x API。

对应 workflow 第 1 步「数据库解析」:表清单/字段统计/外键关系/表样例,
产出供第 2 步选表与第 3 步 SQL 生成使用的上下文材料。
demo 原实现的 check_sql/execute_sql 拆分到 validator.py 与 pipeline 执行器,
并按只读安全要求收紧。
"""
import logging

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select

logger = logging.getLogger(__name__)

# sqlite 内部表,不属于业务数据,选表与生成时排除
INTERNAL_TABLES = {"sqlite_sequence", "sqlite_stat1"}


class DBParser:
    """数据库的解析:初始化时完成全部元数据与轻量统计,之后只读取缓存。"""

    def __init__(self, db_url: str) -> None:
        """初始化并完成逐表解析(表字段统计/外键/样例)。

        db_url: 数据库连接地址
            sqlite: sqlite:///{backend}/data/chinook.db
            mysql:  mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
        """
        # 判断数据库类型
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            raise ValueError(f"不支持的数据库地址: {db_url}")

        # 连接数据库(sqlalchemy 2.x:execute 需经 connect 上下文)
        self.db_url = db_url
        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()
        # 业务表 = 全部表剔除 sqlite 内部表
        self.business_tables = [t for t in self.table_names if t not in INTERNAL_TABLES]

        self._table_fields: dict[str, dict] = {}   # 表 → 字段名 → 元数据+统计
        self._table_sample: dict[str, pd.DataFrame] = {}  # 表 → 样例 DataFrame
        self.foreign_keys: list[dict] = []          # 全库外键关系

        with self.engine.connect() as conn:
            # 依次对每张表解析
            for table_name in self.table_names:
                logger.debug("解析表 %s", table_name)

                # 累计外键
                self.foreign_keys += [
                    {
                        "constrained_table": table_name,
                        "constrained_columns": fk["constrained_columns"],
                        "referred_table": fk["referred_table"],
                        "referred_columns": fk["referred_columns"],
                    }
                    for fk in self.inspector.get_foreign_keys(table_name)
                ]

                # 字段元数据(name/type/nullable/... 保持 inspector 顺序)
                table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
                table_columns = self.inspector.get_columns(table_name)
                self._table_fields[table_name] = {
                    col["name"]: dict(col) for col in table_columns
                }

                # 对当前字段做轻量统计(供 LLM 理解字段语义)
                for column_meta in table_columns:
                    stats = self._table_fields[table_name][column_meta["name"]]

                    # 统计 distinct
                    column_instance = getattr(table_instance.columns, column_meta["name"])
                    query = select(func.count(func.distinct(column_instance)))
                    stats["distinct"] = conn.execute(query).fetchone()[0]

                    # 文本字段统计最高频取值
                    if "char" in str(stats["type"]).lower() or "text" in str(stats["type"]).lower():
                        query = (
                            select(column_instance, func.count().label("cnt"))
                            .group_by(column_instance)
                            .order_by(func.count().desc())
                            .limit(1)
                        )
                        top1 = conn.execute(query).fetchone()
                        stats["mode"] = top1[0] if top1 else None

                    # 统计缺失个数
                    query = select(func.count()).filter(column_instance == None)  # noqa: E711
                    stats["nan_count"] = conn.execute(query).fetchone()[0]

                    # 统计 max / min
                    stats["max"] = conn.execute(
                        select(func.max(column_instance))
                    ).fetchone()[0]
                    stats["min"] = conn.execute(
                        select(func.min(column_instance))
                    ).fetchone()[0]

                    # 任意取值样例(去重取 3 个)
                    rows = conn.execute(select(column_instance).limit(10)).all()
                    random_values = {str(x[0]) for x in rows if x[0] is not None}
                    stats["random"] = list(random_values)[:3]

                # 表样例(前 5 行,按需切)
                sample_rows = conn.execute(select(table_instance).limit(5)).mappings().all()
                self._table_sample[table_name] = pd.DataFrame(
                    [dict(r) for r in sample_rows],
                    columns=[c["name"] for c in table_columns],
                )

    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息(含类型与统计),字段名为行索引。"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库外键关系(constrained → referred)。"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str, n: int = 3) -> pd.DataFrame:
        """获取数据表样例前 n 行。"""
        return self._table_sample[table_name].head(n)
