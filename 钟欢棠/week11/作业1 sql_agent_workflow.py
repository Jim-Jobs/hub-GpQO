# -*- coding: utf-8 -*-
"""
SQL Agent Workflow —— 基于 chinook.db 的完整 SQL 智能问答流程

七步流程：
  ① 数据库解析   DBParser           读取表结构 / 外键 / 字段统计 / 样例
  ② Table 选择   step_table_select  根据用户提问挑选相关表
  ③ SQL 生成     step_generate_sql  根据提问 + 表结构生成 SQL
  ④ SQL 校验     step_validate_sql  校验 SQL 是否可执行
  ⑤ SQL 改写     step_rewrite_sql   校验失败后基于报错信息重写
  ⑥ SQL 执行     step_execute_sql   执行合法 SQL
  ⑦ 结果汇总     step_summarize     把结果组织成自然语言

运行方式：
    python sql_agent_workflow.py

真实 LLM（推荐）：设置环境变量后自动启用
    export OPENAI_API_KEY=sk-xxx
    export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1   # 通义千问
    export LLM_MODEL=qwen-max
    其它兼容 OpenAI 协议的模型（DeepSeek / 豆包 / GPT）只需改 BASE_URL + KEY + MODEL。

离线演示（无 key）：未设置 OPENAI_API_KEY 时自动退化为规则模式（仅覆盖若干典型问法，用于跑通流程）。
"""

import os
import sys
import re
import json
import traceback
from typing import List, Union

import pandas as pd
from sqlalchemy import create_engine, inspect, func, select, Table, MetaData, text

# 兼容 Windows 控制台 GBK，避免打印中文/特殊字符报错
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ======================================================================
# ① 数据库解析
# ======================================================================
class DBParser:
    """解析数据库：表结构、外键关系、字段统计、样例数据。"""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()

        self._table_fields = {}
        self.foreign_keys = []
        self._table_sample = {}

        for table_name in self.table_names:
            print("Table ->", table_name)
            self._table_fields[table_name] = {}

            # 累计外键
            self.foreign_keys += [
                {
                    "constrained_table": table_name,
                    "constrained_columns": x["constrained_columns"],
                    "referred_table": x["referred_table"],
                    "referred_columns": x["referred_columns"],
                }
                for x in self.inspector.get_foreign_keys(table_name)
            ]

            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)
            self._table_fields[table_name] = {x["name"]: x for x in table_columns}

            # 字段统计
            for column_meta in table_columns:
                column_instance = getattr(table_instance.columns, column_meta["name"])
                name = column_meta["name"]

                # distinct 个数
                q = select(func.count(func.distinct(column_instance)))
                self._table_fields[table_name][name]["distinct"] = self.conn.execute(q).fetchone()[0]

                # 出现频率最高的值（文本列）
                field_type = str(self._table_fields[table_name][name]["type"])
                if "text" in field_type.lower() or "char" in field_type.lower():
                    q = (
                        select(column_instance, func.count().label("cnt"))
                        .group_by(column_instance)
                        .order_by(func.count().desc())
                        .limit(1)
                    )
                    self._table_fields[table_name][name]["mode"] = self.conn.execute(q).fetchone()[0]

                # 缺失个数
                q = select(func.count()).filter(column_instance == None)  # noqa: E711
                self._table_fields[table_name][name]["nan_count"] = self.conn.execute(q).fetchone()[0]

                # 最大 / 最小
                self._table_fields[table_name][name]["max"] = self.conn.execute(select(func.max(column_instance))).fetchone()[0]
                self._table_fields[table_name][name]["min"] = self.conn.execute(select(func.min(column_instance))).fetchone()[0]

                # 任意取值（样例）
                q = select(column_instance).limit(10)
                rand_vals = [x[0] for x in self.conn.execute(q).all()]
                rand_vals = list(set(str(x) for x in rand_vals if x is not None))
                self._table_fields[table_name][name]["random"] = rand_vals[:3]

            # 表样例（第一行）
            q = select(table_instance)
            self._table_sample[table_name] = pd.DataFrame([self.conn.execute(q).fetchone()])
            self._table_sample[table_name].columns = [x["name"] for x in table_columns]

    # ---------- 查询接口 ----------
    def get_table_fields(self, table_name) -> pd.DataFrame:
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name) -> pd.DataFrame:
        return self._table_sample[table_name]

    def get_schema_summary(self, table_names: List[str] = None) -> str:
        """生成给 LLM 看的紧凑 schema 描述。"""
        table_names = table_names or self.table_names
        lines = []
        for t in table_names:
            cols = self._table_fields[t]
            col_strs = []
            for cname, meta in cols.items():
                typ = str(meta.get("type"))
                pk = " PK" if meta.get("primary_key") else ""
                col_strs.append(f"{cname}({typ}{pk})")
            lines.append(f"表 {t}: " + ", ".join(col_strs))
        return "\n".join(lines)

    def get_relations_str(self, table_names: List[str] = None) -> str:
        """生成外键关系描述。"""
        table_names = set(table_names) if table_names else set(self.table_names)
        lines = []
        for fk in self.foreign_keys:
            if fk["constrained_table"] in table_names:
                lines.append(
                    f"{fk['constrained_table']}.{fk['constrained_columns']} -> "
                    f"{fk['referred_table']}.{fk['referred_columns']}"
                )
        return "\n".join(lines)

    def check_sql(self, sql) -> Union[bool, str]:
        """校验 SQL 是否可执行，返回 (是否通过, 报错信息)。"""
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql) -> List[tuple]:
        """执行 SQL，返回结果行列表。"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
            rows = list(result)
        return [tuple(r) for r in rows]

    def execute_sql_df(self, sql) -> pd.DataFrame:
        """执行 SQL，返回带列名的 DataFrame。"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
            rows = list(result)
            cols = list(result.keys())
        return pd.DataFrame([tuple(r) for r in rows], columns=cols)


# ======================================================================
# LLM 抽象：真实模型 / 离线规则，两种实现都提供同样的 4 个能力
# ======================================================================
class OpenAILLM:
    """通过 OpenAI 兼容协议调用大模型（通义/DeepSeek/豆包/GPT 通用）。"""

    def __init__(self, api_key=None, base_url=None, model=None):
        import openai
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.model = model or os.environ.get("LLM_MODEL", "qwen-max")
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return completion.choices[0].message.content or ""

    # ---------- ② 选表 ----------
    def select_tables(self, question, schema_summary, relations_str) -> List[str]:
        system = "你是 SQL 选表专家。根据用户提问，从给定的表中选择回答问题所需的表。只输出 JSON 数组，例如 [\"albums\", \"artists\"]。"
        user = (
            f"用户提问：{question}\n\n"
            f"可用表结构：\n{schema_summary}\n\n"
            f"表间外键关系：\n{relations_str or '无'}\n\n"
            "请输出需要的表名 JSON 数组："
        )
        raw = self._chat(system, user)
        return _extract_json_array(raw)

    # ---------- ③ 生成 SQL ----------
    def generate_sql(self, question, schema_summary, relations_str, sample_md) -> str:
        system = "你是 SQL 生成专家。根据用户提问和表结构生成一条 SQLite 可执行的 SQL。只输出 SQL，不要解释。"
        user = (
            f"用户提问：{question}\n\n"
            f"相关表结构：\n{schema_summary}\n\n"
            f"外键关系：\n{relations_str or '无'}\n\n"
            f"表样例数据：\n{sample_md or '无'}\n\n"
            "请直接输出 SQL："
        )
        return _extract_sql(self._chat(system, user))

    # ---------- ⑤ 改写 SQL ----------
    def rewrite_sql(self, question, schema_summary, relations_str, sql, error) -> str:
        system = "你是 SQL 改写专家。原始 SQL 执行报错，请根据报错信息和表结构修正 SQL。只输出修正后的 SQL。"
        user = (
            f"用户提问：{question}\n\n"
            f"相关表结构：\n{schema_summary}\n\n"
            f"外键关系：\n{relations_str or '无'}\n\n"
            f"原始 SQL：\n{sql}\n\n"
            f"报错信息：\n{error}\n\n"
            "请直接输出修正后的 SQL："
        )
        return _extract_sql(self._chat(system, user))

    # ---------- ⑦ 汇总 ----------
    def summarize(self, question, sql, result_md) -> str:
        system = "你是数据结果汇总专家。把用户提问、执行的 SQL 和查询结果组织成简洁的自然语言回答。"
        user = (
            f"用户提问：{question}\n\n"
            f"执行的 SQL：\n{sql}\n\n"
            f"查询结果：\n{result_md}\n\n"
            "请用自然语言回答："
        )
        return self._chat(system, user, temperature=0.3)


class OfflineLLM:
    """离线规则实现（无 API key 时用于演示流程，仅覆盖若干典型问法）。"""

    # 关键词 -> 相关表
    _TABLE_KEYWORDS = [
        (["艺术家", "专辑"], ["artists", "albums"]),
        (["客户", "销售", "账单", "invoice"], ["customers", "invoices"]),
        (["流派", "genre"], ["genres", "tracks"]),
        (["曲目", "track", "歌曲"], ["tracks"]),
        (["专辑"], ["albums"]),
        (["客户"], ["customers"]),
        (["员工", "employee"], ["employees"]),
    ]

    def select_tables(self, question, schema_summary, relations_str) -> List[str]:
        tables = []
        for kws, tabs in self._TABLE_KEYWORDS:
            if any(k in question.lower() for k in kws):
                tables += tabs
        # 去重保序
        return list(dict.fromkeys(tables)) or ["albums"]

    def generate_sql(self, question, schema_summary, relations_str, sample_md) -> str:
        q = question.lower()
        if "艺术家" in q and "专辑" in q:
            return (
                "SELECT a.Name AS ArtistName, COUNT(al.AlbumId) AS AlbumCount "
                "FROM artists a LEFT JOIN albums al ON a.ArtistId = al.ArtistId "
                "GROUP BY a.ArtistId ORDER BY AlbumCount DESC;"
            )
        if "客户" in q and ("销售" in q or "消费" in q or "账单" in q):
            return (
                "SELECT c.FirstName, c.LastName, ROUND(SUM(i.Total), 2) AS Total "
                "FROM customers c JOIN invoices i ON c.CustomerId = i.CustomerId "
                "GROUP BY c.CustomerId ORDER BY Total DESC LIMIT 5;"
            )
        if "流派" in q:
            return (
                "SELECT g.Name AS GenreName, COUNT(t.TrackId) AS TrackCount "
                "FROM genres g LEFT JOIN tracks t ON g.GenreId = t.GenreId "
                "GROUP BY g.GenreId ORDER BY TrackCount DESC;"
            )
        if "曲目" in q or "track" in q or "歌曲" in q:
            return "SELECT COUNT(*) AS TrackCount FROM tracks;"
        if "专辑" in q:
            return "SELECT COUNT(*) AS AlbumCount FROM albums;"
        # 通用兜底：查所选第一张表的前 5 行
        tables = self.select_tables(question, schema_summary, relations_str)
        return f"SELECT * FROM {tables[0]} LIMIT 5;"

    def rewrite_sql(self, question, schema_summary, relations_str, sql, error) -> str:
        # 离线规则简单兜底：直接返回一个安全查询
        tables = self.select_tables(question, schema_summary, relations_str)
        return f"SELECT * FROM {tables[0]} LIMIT 5;"

    def summarize(self, question, sql, result_md) -> str:
        return f"（离线模式）已根据提问执行 SQL 得到如下结果：\n{result_md}"


# ======================================================================
# SQL Agent：编排七步流程
# ======================================================================
class SQLAgent:
    def __init__(self, db_url: str, llm=None, max_retries: int = 3):
        self.parser = DBParser(db_url)
        self.llm = llm or self._build_llm()
        self.max_retries = max_retries

    @staticmethod
    def _build_llm():
        if os.environ.get("OPENAI_API_KEY"):
            return OpenAILLM()
        print("[WARN] 未检测到 OPENAI_API_KEY，使用离线规则模式（仅用于演示流程）。")
        return OfflineLLM()

    # ---------- 各步骤 ----------
    def _context(self, tables):
        """构建某几张表的上下文（schema + 外键 + 样例）。"""
        schema = self.parser.get_schema_summary(tables)
        relations = self.parser.get_relations_str(tables)
        sample_parts = []
        for t in tables:
            sample_parts.append(f"### {t}\n" + _df_to_markdown(self.parser.get_table_sample(t)))
        return schema, relations, "\n\n".join(sample_parts)

    def step_table_select(self, question) -> List[str]:
        schema = self.parser.get_schema_summary()
        relations = self.parser.get_relations_str()
        return self.llm.select_tables(question, schema, relations)

    def step_generate_sql(self, question, tables) -> str:
        schema, relations, sample = self._context(tables)
        return self.llm.generate_sql(question, schema, relations, sample)

    def step_validate_sql(self, sql):
        return self.parser.check_sql(sql)

    def step_rewrite_sql(self, question, tables, sql, error) -> str:
        schema, relations, sample = self._context(tables)
        return self.llm.rewrite_sql(question, schema, relations, sql, error)

    def step_execute_sql(self, sql):
        return self.parser.execute_sql_df(sql)

    def step_summarize(self, question, sql, result_df: pd.DataFrame) -> str:
        result_md = _df_to_markdown(result_df)
        return self.llm.summarize(question, sql, result_md)

    # ---------- 主流程 ----------
    def run(self, question: str, verbose: bool = True) -> dict:
        trace = []

        # ② 选表
        tables = self.step_table_select(question)
        trace.append(f"[② 选表] {tables}")

        # ③ 生成 SQL
        sql = self.step_generate_sql(question, tables)
        trace.append(f"[③ 生成] {sql}")

        # ④⑤ 校验 + 改写循环
        final_sql = sql
        for attempt in range(1, self.max_retries + 1):
            ok, err = self.step_validate_sql(final_sql)
            if ok:
                trace.append(f"[④ 校验] 通过（第 {attempt} 次）")
                break
            trace.append(f"[④ 校验] 失败（第 {attempt} 次）：{err.splitlines()[-1] if err else err}")
            final_sql = self.step_rewrite_sql(question, tables, final_sql, err)
            trace.append(f"[⑤ 改写] {final_sql}")
        else:
            result = {
                "question": question, "tables": tables, "sql": None,
                "result": None, "answer": "SQL 改写多次仍失败，请人工介入。", "trace": trace,
            }
            if verbose:
                print("\n".join(trace))
            return result

        # ⑥ 执行
        result_df = self.step_execute_sql(final_sql)
        trace.append(f"[⑥ 执行] 返回 {len(result_df)} 行")

        # ⑦ 汇总
        answer = self.step_summarize(question, final_sql, result_df)
        trace.append(f"[⑦ 汇总] {answer}")

        if verbose:
            print("\n".join(trace))

        return {
            "question": question,
            "tables": tables,
            "sql": final_sql,
            "result": result_df,
            "answer": answer,
            "trace": trace,
        }


# ======================================================================
# 工具函数
# ======================================================================
def _df_to_markdown(df: pd.DataFrame, max_rows: int = 20) -> str:
    """把 DataFrame 转成 markdown 表格（不依赖 tabulate）。"""
    if df is None or df.empty:
        return "（空结果）"
    df = df.head(max_rows).where(pd.notnull(df), None)
    cols = [str(c) for c in df.columns]
    rows = df.values.tolist()

    def fmt(v):
        if v is None:
            return ""
        return str(v).replace("|", "\\|")

    def row_to_str(cells):
        return "| " + " | ".join(fmt(c) for c in cells) + " |"

    header = row_to_str(cols)
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join(row_to_str(r) for r in rows)
    return "\n".join([header, sep, body])


def _extract_json_array(text: str) -> List[str]:
    """从 LLM 输出中稳健地抽取 JSON 数组。"""
    m = re.search(r"\[[^\]]*\]", text, re.DOTALL)
    if m:
        try:
            return [x for x in json.loads(m.group(0)) if x]
        except Exception:
            pass
    parts = re.split(r"[,，\n]", text)
    return [p.strip().strip("\"'`[] ") for p in parts if p.strip()]


def _extract_sql(text: str) -> str:
    """从 LLM 输出中抽取 SQL（剥离 markdown 代码块）。"""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip().strip("`").strip()


# ======================================================================
# 入口
# ======================================================================
def run_demo():
    db_url = "sqlite:///" + os.path.join(os.path.dirname(__file__), "chinook.db")
    agent = SQLAgent(db_url)

    questions = [
        "每个艺术家分别有多少张专辑？",
        "销售额最高的 5 个客户是谁？",
        "每个流派分别有多少首曲目？",
        "曲目总数是多少？",
    ]
    for q in questions:
        print("\n" + "=" * 60)
        print("用户提问：", q)
        res = agent.run(q)
        print("最终回答：", res["answer"])
        if res["result"] is not None:
            print("结果预览：")
            print(_df_to_markdown(res["result"].head()))


if __name__ == "__main__":
    run_demo()
