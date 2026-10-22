# -*- coding: utf-8 -*-
"""
SQL Agent Workflow
==================

参考 sql-agent.ipynb（数据库解析 DBParser + 提示词）与 code-agent.ipynb（生成→执行→
反思→重试→汇总的闭环）实现的完整 SQL Agent 工作流。

把一条自然语言问题转换为可执行 SQL，并最终返回自然语言答案。

工作流步骤（对应下面的方法）：
    1. 数据库解析   DBParser.parse        —— 解析表结构、字段统计、外键、样例
    2. table 选择   SQLAgent.select_tables —— 根据问题选择合适的表
    3. sql 生成     SQLAgent.generate_sql  —— 根据问题 + 表结构生成 SQL
    4. sql 校验     DBParser.check_sql      —— 校验 SQL 是否可执行
    5. sql 改写     SQLAgent.rewrite_sql    —— 校验失败时根据报错信息改写 SQL
    6. sql 执行     DBParser.execute_sql    —— 执行 SQL 获取结果
    7. 结果汇总     SQLAgent.summarize      —— 把问题 + SQL + 结果汇总成自然语言

依赖安装：
    pip install sqlalchemy pandas openai

运行前配置（不把密钥硬编码进代码，通过环境变量注入）：
    $env:OPENAI_API_KEY="sk-xxxx"                    # 你的密钥
    $env:OPENAI_BASE_URL="https://api.deepseek.com"  # DeepSeek；DashScope 用 https://dashscope.aliyuncs.com/compatible-mode/v1
    $env:MODEL="deepseek-v4-pro"                     # 模型名，默认 qwen-max

运行：
    python sql_agent_workflow.py
"""

from __future__ import annotations

import os
import re
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from openai import OpenAI
from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)


# --------------------------------------------------------------------------- #
# 模块一：数据库解析
# --------------------------------------------------------------------------- #
class DBParser:
    """数据库解析器：把底层数据库"翻译"成 LLM 能理解的文本信息。

    对每张表收集：
        - 表名、字段名、字段类型、是否可空、是否主键
        - 字段 distinct 数量（判断是否唯一/枚举）
        - 文本字段出现最频繁的值（mode）
        - 空值数量、最大值、最小值、样例取值
        - 表之间的主外键关系
        - 每张表的样例行
    """

    def __init__(self, db_url: str) -> None:
        """初始化并解析数据库。

        db_url 示例：
            sqlite: sqlite:///./chinook.db
            mysql : mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
        """
        # 判断数据库类型
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        # 连接数据库
        self.db_url = db_url
        self.engine = create_engine(db_url, echo=False)

        # 查看表名（SQLAlchemy inspector 会过滤 sqlite 系统表）
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()

        self._table_fields: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.foreign_keys: List[Dict[str, Any]] = []
        self._table_sample: Dict[str, pd.DataFrame] = {}

        self.parse()

    # ------------------------------------------------------------------ #
    def parse(self) -> None:
        """对每张表的每个字段做统计。"""
        with self.engine.connect() as conn:
            for table_name in self.table_names:
                print("Table ->", table_name)

                # 累计外键关系
                self.foreign_keys += [
                    {
                        "constrained_table": table_name,
                        "constrained_columns": x["constrained_columns"],
                        "referred_table": x["referred_table"],
                        "referred_columns": x["referred_columns"],
                    }
                    for x in self.inspector.get_foreign_keys(table_name)
                ]

                # 获取当前表的字段信息
                table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
                table_columns = self.inspector.get_columns(table_name)
                self._table_fields[table_name] = {x["name"]: x for x in table_columns}

                # 对当前表的每个字段做统计
                for column_meta in table_columns:
                    column_name = column_meta["name"]
                    column_instance = getattr(table_instance.columns, column_name)

                    # 统计 distinct 数量
                    distinct_count = conn.execute(
                        select(func.count(func.distinct(column_instance)))
                    ).fetchone()[0]
                    self._table_fields[table_name][column_name]["distinct"] = distinct_count

                    # 文本字段统计出现最频繁的值（mode）
                    field_type = str(column_meta["type"])
                    if "text" in field_type.lower() or "char" in field_type.lower():
                        top1 = conn.execute(
                            select(column_instance, func.count().label("count"))
                            .group_by(column_instance)
                            .order_by(func.count().desc())
                            .limit(1)
                        ).fetchone()
                        self._table_fields[table_name][column_name]["mode"] = (
                            top1[0] if top1 is not None else None
                        )

                    # 统计空值数量
                    nan_count = conn.execute(
                        select(func.count()).where(column_instance.is_(None))
                    ).fetchone()[0]
                    self._table_fields[table_name][column_name]["nan_count"] = nan_count

                    # 统计最大/最小值
                    self._table_fields[table_name][column_name]["max"] = conn.execute(
                        select(func.max(column_instance))
                    ).fetchone()[0]
                    self._table_fields[table_name][column_name]["min"] = conn.execute(
                        select(func.min(column_instance))
                    ).fetchone()[0]

                    # 任意样例取值
                    random_values = [
                        str(x[0])
                        for x in conn.execute(select(column_instance).limit(10)).all()
                        if x[0] is not None
                    ]
                    self._table_fields[table_name][column_name]["random"] = list(
                        dict.fromkeys(random_values)
                    )[:3]

                # 获取表样例（第一行）
                first_row = conn.execute(select(table_instance).limit(1)).fetchone()
                sample_df = pd.DataFrame([first_row]) if first_row is not None else pd.DataFrame()
                sample_df.columns = [x["name"] for x in table_columns]
                self._table_sample[table_name] = sample_df

    # ------------------------------------------------------------------ #
    # 对外查询接口
    # ------------------------------------------------------------------ #
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段统计信息（schema 描述）。"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库的主外键关系。"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取数据表样例。"""
        return self._table_sample[table_name]

    def describe_tables(self) -> str:
        """输出所有表的名称和字段清单，供"table 选择"使用。"""
        lines = []
        for t in self.table_names:
            cols = list(self._table_fields[t].keys())
            lines.append(f"- {t}: {cols}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # SQL 校验 / 执行
    # ------------------------------------------------------------------ #
    def check_sql(self, sql: str) -> Tuple[bool, str]:
        """校验 SQL 是否可执行（第 4 步：sql 校验）。

        返回 (是否可执行, 报错信息)。
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> List[Any]:
        """执行 SQL 并返回结果（第 6 步：sql 执行）。"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
            return list(result)


# --------------------------------------------------------------------------- #
# 模块二：SQL Agent
# --------------------------------------------------------------------------- #
class SQLAgent:
    """SQL Agent：把自然语言问题转换为 SQL 并返回自然语言答案。"""

    # 各类提示词模板
    _TABLE_SELECT_PROMPT = """你是一名专业的 SQL 专家。请根据用户提问，从下面的可选数据表中，
选出回答问题所需要的表。只输出表名（多个表用英文逗号分隔），不要输出任何其他内容。

用户提问：{question}

可选数据表及字段：
{table_desc}
"""

    _SQL_GEN_PROMPT = """你是一名专业的 SQL 专家。请根据用户提问和所选数据表的结构，
生成一条正确的 SQL 查询语句。直接输出 SQL 语句（可以用 ```sql ... ``` 包裹），不要输出其他解释。

用户提问：{question}

数据表结构（schema）：
{schema}

数据表样例：
{sample}

表关系（外键）：
{relations}
"""

    _SQL_REWRITE_PROMPT = """你生成的 SQL 执行时报错，请根据报错信息修正 SQL 语句。
直接输出修正后的 SQL 语句（可以用 ```sql ... ``` 包裹），不要输出其他解释。

用户提问：{question}

原始 SQL：
{sql}

报错信息：
{error}

数据表结构（schema）：
{schema}
"""

    _SUMMARY_PROMPT = """你是一名数据分析师。请把下面的问题、SQL 和查询结果，
汇总成一句通顺的自然语言回答。

用户提问：{question}

执行 SQL：{sql}

查询结果：{result}
"""

    def __init__(
        self,
        db_url: str,
        model: str = "qwen-max",
        max_retries: int = 5,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """初始化 SQL Agent。

        api_key / base_url 默认从环境变量 OPENAI_API_KEY / OPENAI_BASE_URL 读取，
        不把密钥硬编码进代码。
        """
        self.db = DBParser(db_url)
        self.model = model
        self.max_retries = max_retries

        api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------ #
    # 底层工具
    # ------------------------------------------------------------------ #
    def llm(self, messages: List[Dict[str, str]], temperature: float = 0.1) -> str:
        """调用大模型，返回文本内容。"""
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            return completion.choices[0].message.content or ""
        except Exception:
            print("调用大模型失败：")
            traceback.print_exc()
            return ""

    @staticmethod
    def extract_sql(text: str) -> str:
        """从大模型回答中抽取 SQL（处理 ```sql 代码块 或 纯文本）。"""
        if not text:
            return ""
        match = re.search(r"```(?:sql)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _parse_table_names(self, raw: str) -> List[str]:
        """把大模型返回的表名解析成合法的表名列表。"""
        if not raw:
            return []
        candidates = re.split(r"[,，、\s]+", raw.strip())
        return [c for c in candidates if c in self.db.table_names]

    def _build_context(self, tables: List[str]) -> Tuple[str, str, str]:
        """根据选中的表，构造 schema / 样例 / 外键 三段上下文。"""
        schema = "\n\n".join(
            self.db.get_table_fields(t).to_markdown() for t in tables
        )
        sample = "\n\n".join(
            self.db.get_table_sample(t).to_markdown() for t in tables
        )
        relations = self.db.get_data_relations()
        # 只保留与所选表相关的外键
        related = relations[
            relations["constrained_table"].isin(tables)
            | relations["referred_table"].isin(tables)
        ]
        relations_md = related.to_markdown() if not related.empty else "无"
        return schema, sample, relations_md

    # ------------------------------------------------------------------ #
    # 工作流各步骤
    # ------------------------------------------------------------------ #
    def select_tables(self, question: str) -> List[str]:
        """第 2 步：table 选择。"""
        prompt = self._TABLE_SELECT_PROMPT.format(
            question=question, table_desc=self.db.describe_tables()
        )
        raw = self.llm([{"role": "user", "content": prompt}])
        return self._parse_table_names(raw)

    def generate_sql(self, question: str, tables: List[str]) -> str:
        """第 3 步：sql 生成。"""
        schema, sample, relations = self._build_context(tables)
        prompt = self._SQL_GEN_PROMPT.format(
            question=question, schema=schema, sample=sample, relations=relations
        )
        raw = self.llm([{"role": "user", "content": prompt}])
        return self.extract_sql(raw)

    def rewrite_sql(self, question: str, sql: str, error: str, schema: str) -> str:
        """第 5 步：sql 改写（校验失败时根据报错信息修正）。"""
        prompt = self._SQL_REWRITE_PROMPT.format(
            question=question, sql=sql, error=error, schema=schema
        )
        raw = self.llm([{"role": "user", "content": prompt}])
        return self.extract_sql(raw)

    def validate_and_rewrite(
        self, question: str, sql: str, tables: List[str]
    ) -> Tuple[Optional[str], str]:
        """第 4 + 5 步：sql 校验 + 改写，循环直到通过或超过重试次数。

        返回 (最终 SQL, 报错信息)。最终 SQL 为 None 表示全部失败。
        """
        schema, _, _ = self._build_context(tables)
        for i in range(self.max_retries):
            ok, err = self.db.check_sql(sql)
            if ok:
                print(f"[4/7] sql 校验：通过")
                return sql, ""
            print(f"[4/7] sql 校验：失败（第 {i + 1}/{self.max_retries} 次）")
            print(f"[5/7] sql 改写中……")
            sql = self.rewrite_sql(question, sql, err, schema)
            print(f"[5/7] 改写后的 SQL：{sql}")
        return None, err

    def execute_sql(self, sql: str) -> str:
        """第 6 步：sql 执行，并把结果转成字符串。"""
        rows = self.db.execute_sql(sql)
        return str(rows)

    def summarize(self, question: str, sql: str, result: str) -> str:
        """第 7 步：结果汇总。"""
        prompt = self._SUMMARY_PROMPT.format(
            question=question, sql=sql, result=result
        )
        return self.llm([{"role": "user", "content": prompt}], temperature=0.7)

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def run(self, question: str) -> Dict[str, Any]:
        """运行完整工作流，返回每一步的中间结果。"""
        print("=" * 60)
        print("用户提问：", question)
        print("=" * 60)

        print("[1/7] 数据库解析：完成（共 %d 张表）" % len(self.db.table_names))

        tables = self.select_tables(question)
        print("[2/7] table 选择：", tables)
        if not tables:
            return {"question": question, "error": "未能选择出相关数据表"}

        sql = self.generate_sql(question, tables)
        print("[3/7] sql 生成：", sql)

        sql, err = self.validate_and_rewrite(question, sql, tables)
        if sql is None:
            return {"question": question, "tables": tables, "error": err}

        result = self.execute_sql(sql)
        print("[6/7] sql 执行：", result)

        answer = self.summarize(question, sql, result)
        print("[7/7] 结果汇总：", answer)

        return {
            "question": question,
            "tables": tables,
            "sql": sql,
            "result": result,
            "answer": answer,
        }


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    db_url = os.environ.get("DB_URL", "sqlite:///./chinook.db")
    model = os.environ.get("MODEL", "qwen-max")
    agent = SQLAgent(db_url=db_url, model=model)

    # 可以替换成任意问题，例如：
    #   "每个艺术家发行了多少张专辑？"
    #   "一共有多少张专辑？"
    #   "客户数量最多的国家是哪一个？"
    question = "每个艺术家（ArtistId）发行了多少张专辑？"
    result = agent.run(question)

    print("\n" + "=" * 60)
    print("最终结果：")
    print(result)


if __name__ == "__main__":
    main()
