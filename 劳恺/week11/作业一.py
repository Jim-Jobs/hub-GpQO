from typing import Union, List, Dict, Any
import traceback
import re
import pandas as pd
from sqlalchemy import (
    create_engine,
    inspect,
    func,
    select,
    text,
    Table,
    MetaData,
)
import os
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_openai import ChatOpenAI

class DBParser:
    """数据库解析器，供 SQL Agent workflow 使用。"""

    def __init__(self, db_url: str, sample_limit: int = 5) -> None:
        self.db_url = db_url
        self.sample_limit = sample_limit

        if "sqlite" in db_url.lower():
            self.db_type = "sqlite"
        elif "mysql" in db_url.lower():
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        self.engine = create_engine(db_url, echo=False)

        with self.engine.connect() as conn:
            inspector = inspect(self.engine)

            self.table_names: List[str] = inspector.get_table_names()
            self.foreign_keys: List[Dict[str, Any]] = []
            self._table_fields: Dict[str, Dict[str, Dict[str, Any]]] = {}
            self._table_samples: Dict[str, pd.DataFrame] = {}

            for table_name in self.table_names:
                self._table_fields[table_name] = {}
                # 累计外键
                fk_list = inspector.get_foreign_keys(table_name)
                for fk in fk_list:
                    self.foreign_keys.append(
                        {
                            "constrained_table": table_name,
                            "constrained_columns": fk.get("constrained_columns", []),
                            "referred_table": fk.get("referred_table"),
                            "referred_columns": fk.get("referred_columns", []),
                        }
                    )
                # 获取当前表的字段信息
                table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
                columns = inspector.get_columns(table_name)
                for col in columns:
                    col_name = col["name"]
                    self._table_fields[table_name][col_name] = {
                        "name": col_name,
                        "type": str(col.get("type", "")),
                        "nullable": col.get("nullable", True),
                        "default": col.get("default"),
                        "comment": col.get("comment"),
                    }

                # 只取少量样例，避免初始化过慢
                try:
                    query = select(table_instance).limit(self.sample_limit)
                    rows = conn.execute(query).fetchall()
                    if rows:
                        df = pd.DataFrame(
                            rows, columns=[c["name"] for c in columns]
                        )
                        self._table_samples[table_name] = df
                except Exception:
                    pass

    # -------------------------
    # 基础元数据
    # -------------------------

    def get_table_names(self) -> List[str]:
        return self.table_names

    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """返回表字段信息 DataFrame。"""
        fields = self._table_fields.get(table_name)
        if not fields:
            return pd.DataFrame()

        rows = []
        for col_name, meta in fields.items():
            rows.append(
                {
                    "column_name": col_name,
                    "type": meta.get("type", ""),
                    "nullable": meta.get("nullable", True),
                    "default": meta.get("default"),
                    "comment": meta.get("comment"),
                }
            )
        return pd.DataFrame(rows)

    def get_data_relations(self) -> pd.DataFrame:
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        df = self._table_samples.get(table_name)
        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()

    # -------------------------
    # SQL 校验
    # -------------------------

    @staticmethod
    def _is_readonly_sql(sql: str) -> bool:
        normalized = re.sub(r"--.*", "", sql).strip().lower()
        if not normalized:
            return False
        first_token = normalized.split(None, 1)[0]
        return first_token == "select"

    def check_sql(self, sql: str) -> tuple[bool, str]:
        """先做只读校验，再用 EXPLAIN 检查 SQL 是否能被数据库解析。"""
        if not self._is_readonly_sql(sql):
            return False, "仅允许执行 SELECT 查询"

        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN {sql}"))
                return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    # -------------------------
    # SQL 执行
    # -------------------------

    def execute_sql(self, sql: str, max_rows: int = 200) -> Dict[str, Any]:
        """执行只读 SQL，返回结构化结果。"""
        if not self._is_readonly_sql(sql):
            return {
                "success": False,
                "error": "仅允许执行 SELECT 查询",
                "columns": [],
                "rows": [],
            }

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))

                columns = list(result.keys()) if result.returns_rows else []
                rows: List[Any] = []

                if result.returns_rows:
                    fetched = result.fetchmany(max_rows)
                    rows = [list(r) for r in fetched]

                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": len(rows) >= max_rows,
                }

        except Exception:
            return {
                "success": False,
                "error": traceback.format_exc(),
                "columns": [],
                "rows": [],
            }


def _clean_sql(text: str) -> str:
    """清理 LLM 生成的 SQL，去掉 markdown 代码块和前缀。"""
    text = text.strip()
    match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    text = re.sub(r"^SQLQuery:\s*", "", text, flags=re.IGNORECASE).strip()
    return text


def _sql_generation_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_template(
        """
        你是 SQL 专家。请根据用户问题和数据库表结构，生成一条语法正确的 {dialect} SQL 查询。
        【约束】
        1. 只输出 SQL 语句，不要解释，不要输出 markdown 代码块。
        2. 只使用下面提供的表，不要假设其他表存在。
        3. 如果用户没有明确要求全部数据，必须加上 LIMIT 100。
        4. 只允许生成 SELECT 查询。
        
        【表结构】
        {schema}
        
        【用户问题】
        {question}
        """
    )


def _answer_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_template(
        """
        请根据用户问题、生成的 SQL 和查询结果，给出简洁、准确的自然语言回答。
        用户问题：{question}
        SQL 查询：{query}
        查询结果：{result}
        如果查询结果为空，请明确说明没有查到数据。
        回答："""
    )


class SQLAgentWorkflow:
    """
    用户问题 -> 生成 SQL -> 校验 -> 改写 -> 执行 -> 结果汇总
    """

    def __init__(
            self,
            db_parser: "DBParser",
            llm: ChatOpenAI | None = None,
            max_retry: int = 2,
    ) -> None:
        self.db = db_parser
        self.llm = llm
        self.max_retry = max_retry
        # 生成 SQL 的链
        self.generate_sql_chain = (
                _sql_generation_prompt()
                | self.llm
                | StrOutputParser()
                | RunnableLambda(_clean_sql)
        )

        # 最终回答链
        self.answer_chain = _answer_prompt() | self.llm

    # -------------------------
    # 内部工具方法
    # -------------------------

    def _schema_for_prompt(self, question: str) -> str:
        """
        简单版：把所有表结构塞进 prompt。
        生产环境建议改成：先根据问题筛选相关表，再只注入这些表的 schema。
        """
        parts = []
        for table_name in self.db.get_table_names():
            fields_df = self.db.get_table_fields(table_name)
            sample_df = self.db.get_table_sample(table_name)

            field_lines = []
            for _, row in fields_df.iterrows():
                field_lines.append(
                    f"- {row['column_name']} ({row.get('type', '')})"
                )

            parts.append(f"表 {table_name}:\n" + "\n".join(field_lines))

            if not sample_df.empty:
                parts.append(f"示例数据:\n{sample_df.head(3).to_string(index=False)}")

        return "\n\n".join(parts)

    def _generate_sql(self, question: str, schema: str, error_context: str = "") -> str:
        prompt_vars: Dict[str, Any] = {
            "dialect": self.db.db_type,
            "schema": schema,
            "question": question,
        }

        if error_context:
            prompt_vars["question"] = (
                f"{question}\n\n"
                f"上一版 SQL 执行失败，错误信息：{error_context}\n"
                f"请修正 SQL。"
            )

        sql = self.generate_sql_chain.invoke(prompt_vars)
        return sql.strip()

    # -------------------------
    # 主流程
    # -------------------------

    def run(self, question: str) -> Dict[str, Any]:
        # 1. 数据库解析 / 获取 schema
        schema = self._schema_for_prompt(question)

        # 2. 生成 SQL
        sql = self._generate_sql(question, schema)

        # 3. SQL 校验 + 改写 + 执行
        last_error = ""
        result = {"success": False, "error": "未执行"}  # ← 加上这一行

        for i in range(self.max_retry + 1):
            ok, msg = self.db.check_sql(sql)
            if not ok:
                last_error = msg
                sql = self._generate_sql(question, schema, error_context=last_error)
                continue

            result = self.db.execute_sql(sql)
            if result.get("success"):
                break

            last_error = result.get("error", "执行失败")
            sql = self._generate_sql(question, schema, error_context=last_error)

        # 4. 结果汇总
        if not result.get("success"):
            return {
                "question": question,
                "sql": sql,
                "success": False,
                "error": last_error,
                "answer": f"查询失败：{last_error}",
            }

        answer_resp = self.answer_chain.invoke(
            {
                "question": question,
                "query": sql,
                "result": str(result["rows"]),
            }
        )

        return {
            "question": question,
            "sql": sql,
            "success": True,
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "answer": answer_resp.content if hasattr(answer_resp, "content") else str(answer_resp),
        }


def main():
    db_parser = DBParser("sqlite:///chinook.db")
    llm = ChatOpenAI(
        model="MiniMax-M3",
        temperature=0,
        api_key=os.environ.get("MINIMAX_API_KEY"),
        base_url="https://api.minimaxi.com/v1",
        extra_body={"thinking": {"type": "disabled"}},
    )

    agent = SQLAgentWorkflow(db_parser=db_parser, llm=llm)

    result = agent.run("每个音乐流派有多少首歌曲？")
    print(result["answer"])
    print(result["sql"])
    print(result["rows"])
if __name__ == "__main__":
    main()
