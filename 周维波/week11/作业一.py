# -*- coding: utf-8 -*-
"""
SQL Agent 完整工作流实现
================================================================================

完整流程（7 个阶段）:
    1. 数据库解析   DBParser          - 借助 SQLAlchemy 解析表、字段、外键、统计量、样例
    2. Table 选择   SQLTableSelectAgent - 结合用户提问 + parse_table 工具选择相关表 (openai-agents SDK)
    3. SQL 生成     SQLGeneratorAgent  - 结合问题 + 表 schema/样例/外键 生成 SQL
    4. SQL 校验     parser.check_sql  - 语法级校验，判断 SQL 能否执行
    5. SQL 改写     SQLRewriteAgent   - 校验失败时, 基于 原问题+原SQL+报错信息 改写 SQL (可多轮重试)
    6. SQL 执行     parser.execute_sql - 执行校验通过的 SQL
    7. 结果汇总     SQLResultSummaryAgent - 将 问题+SQL+结果 组织为自然语言回答



用法:
    from sql_agent import SQLAgent
    agent = SQLAgent("sqlite:///./chinook.db")
    result = agent.ask("销售额最高的前5位客户是谁？")
    print(result["answer"])
"""

import os
import re
import time
import asyncio
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    func,
    inspect,
    select,
    text,
)

# =================================================================================
# 0. LLM 客户端配置 (DashScope OpenAI 兼容接口)
# =================================================================================

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TABLE_SELECT_MODEL = "qwen-max"   # 表选择 agent 使用的模型
DEFAULT_LLM_MODEL = "qwen-plus"           # 生成/改写/汇总使用的模型


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """解析 API Key: 优先使用显式传入, 其次读取环境变量"""
    api_key = api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "未找到 API Key。请通过环境变量 DASHSCOPE_API_KEY (或 OPENAI_API_KEY) 配置, "
            "或在初始化 SQLAgent 时传入 api_key 参数。"
        )
    return api_key


class LLMClient:
    """大模型客户端封装: 基于 openai SDK 调用 DashScope OpenAI 兼容接口"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_LLM_MODEL,
    ) -> None:
        import openai

        self.api_key = resolve_api_key(api_key)
        self.base_url = base_url
        self.model = model
        # 注意: 同时写入环境变量, 供 openai-agents SDK 读取
        os.environ["OPENAI_API_KEY"] = self.api_key
        os.environ["OPENAI_BASE_URL"] = self.base_url
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    # ------------------------------------------------------------------
    def ask(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        """单轮对话, 返回模型回复文本"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ]
        try:
            completion = self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return _extract_text(completion.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001
            # 调用失败时输出错误, 返回空串 (与参考 notebook 的 ask_gpt 行为一致)
            print(f"[LLMClient] 调用大模型失败: {exc}")
            return ""

    # ------------------------------------------------------------------
    def ask_with_messages(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        """多轮对话, 由调用方构造 messages 列表"""
        try:
            completion = self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return _extract_text(completion.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001
            print(f"[LLMClient] 调用大模型失败: {exc}")
            return ""


def _extract_text(content: Any) -> str:
    """兼容 openai SDK 不同版本对 message.content 的返回形式(str / list[ContentPart])"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif getattr(item, "type", None) == "text":
                parts.append(str(getattr(item, "text", "")))
        return "".join(parts)
    return str(content)


# =================================================================================
# 1. 数据库解析 (DBParser)
# =================================================================================

class DBParser:
    """DBParser 数据库的解析

    解析内容:
        - 全部数据表名称
        - 每张表的字段信息(类型/可空/主键) + 统计量(distinct/mode/nan/max/min/随机值)
        - 数据库外键关系
        - 每张表的样例数据(第一行)

    用法:
        parser = DBParser('sqlite:///./chinook.db')
        parser.get_data_relations()
        parser.get_table_fields('albums')
        parser.get_table_sample('albums')
        parser.check_sql('SELECT COUNT(*) FROM albums;')
        parser.execute_sql('SELECT COUNT(*) FROM albums;')
    """

    def __init__(self, db_url: str, verbose: bool = False) -> None:
        """
        初始化
        db_url: 数据库连接地址
            - sqlite: sqlite:///./chinook.db
            - mysql : mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
        """
        # 判断数据库类型
        self.db_type = "sqlite" if "sqlite" in db_url else ("mysql" if "mysql" in db_url else "unknown")

        # 连接数据库
        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.db_url = db_url

        # 获取数据表信息
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()  # 过滤了 sqlite_sequence 等内部表

        self._table_fields: Dict[str, Dict[str, dict]] = {}  # 数据表字段
        self.foreign_keys: List[Dict[str, Any]] = []          # 数据库外键
        self._table_sample: Dict[str, pd.DataFrame] = {}      # 数据表样例

        # 依次对每张表进行解析
        for table_name in self.table_names:
            if verbose:
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

            # 获取当前表的字段信息
            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)
            self._table_fields[table_name] = {x["name"]: dict(x) for x in table_columns}

            # 对每个字段进行统计
            for column_meta in table_columns:
                column_instance = getattr(table_instance.columns, column_meta["name"])
                field = self._table_fields[table_name][column_meta["name"]]

                # 统计 distinct 个数
                distinct_count = self.conn.execute(
                    select(func.count(func.distinct(column_instance)))
                ).fetchone()[0]
                field["distinct"] = distinct_count

                # 统计最频繁取值 mode (仅文本类字段)
                field_type = str(field["type"]).lower()
                if "text" in field_type or "char" in field_type:
                    top1_value = self.conn.execute(
                        select(column_instance, func.count().label("count"))
                        .group_by(column_instance)
                        .order_by(func.count().desc())
                        .limit(1)
                    ).fetchone()[0]
                    field["mode"] = top1_value

                # 统计 missing 个数
                nan_count = self.conn.execute(
                    select(func.count()).filter(column_instance == None)  # noqa: E711
                ).fetchone()[0]
                field["nan_count"] = nan_count

                # 统计 max / min
                max_value = self.conn.execute(select(func.max(column_instance))).fetchone()[0]
                min_value = self.conn.execute(select(func.min(column_instance))).fetchone()[0]
                field["max"] = max_value
                field["min"] = min_value

                # 任意取值(前10条去重, 取前3)
                random_value = [
                    str(x) for x in self.conn.execute(select(column_instance).limit(10)).all()
                    if x[0] is not None
                ]
                random_value = list(dict.fromkeys(random_value))[:3]
                field["random"] = random_value

            # 获取表样例(第一行)
            sample_row = self.conn.execute(select(table_instance).limit(1)).fetchone()
            if sample_row is not None:
                sample_df = pd.DataFrame([list(sample_row)])
                sample_df.columns = [x["name"] for x in table_columns]
                self._table_sample[table_name] = sample_df

    # ------------------------------------------------------------------
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息 DataFrame (行=字段, 列=字段属性+统计量)"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库连接信息(主键与外键关系)"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取数据表样例"""
        return self._table_sample[table_name]

    def get_table_schema_markdown(self, table_name: str) -> str:
        """获取表 schema 的 markdown 描述(供 LLM 提示词使用)"""
        return self.get_table_fields(table_name).to_markdown()

    def get_table_sample_markdown(self, table_name: str) -> str:
        """获取表样例的 markdown 描述(供 LLM 提示词使用)"""
        return self.get_table_sample(table_name).to_markdown()

    def get_relations_markdown(self) -> str:
        """获取外键关系的 markdown 描述(供 LLM 提示词使用)"""
        return self.get_data_relations().to_markdown()

    # ------------------------------------------------------------------
    def check_sql(self, sql: str) -> Tuple[bool, str]:
        """检查 SQL 是否合理(能否执行)

        返回: (是否可以运行, 报错信息)
        """
        try:
            with self.engine.begin() as conn:  # begin() 会自动提交和关闭
                conn.execute(text(sql))
            return True, "ok"
        except Exception:
            err_msg = traceback.format_exc()
            return False, err_msg

    # ------------------------------------------------------------------
    def execute_sql(self, sql: str) -> list:
        """运行 SQL, 返回查询结果行列表"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
        return list(result)

    @staticmethod
    def format_result(rows: list, max_rows: int = 50) -> str:
        """将执行结果格式化为文本(供结果汇总阶段使用)"""
        if not rows:
            return "(空结果)"
        lines = []
        for row in rows[:max_rows]:
            lines.append(" | ".join(str(v) for v in row))
        if len(rows) > max_rows:
            lines.append(f"... (共 {len(rows)} 行, 仅展示前 {max_rows} 行)")
        return "\n".join(lines)


# =================================================================================
# 2. Table 选择 (SQLTableSelectAgent) - 基于 openai-agents SDK
# =================================================================================

class SQLTableSelectAgent:
    """表选择智能体

    借助 openai-agents SDK 的 Agent + function_tool 机制:
        - 给模型提供 parse_table 工具, 模型可自行查看任意表的结构再决定用哪张表;
        - 结合用户提问, 从全部候选表中挑选出回答所需的表。
    """

    def __init__(
        self,
        parser: DBParser,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_TABLE_SELECT_MODEL,
    ) -> None:
        from typing import Annotated
        from agents import Agent, function_tool

        self.parser = parser
        self.model = model
        # 设置环境变量供 agents SDK 默认 provider 读取
        api_key = resolve_api_key(api_key)
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["OPENAI_BASE_URL"] = base_url

        @function_tool
        def parse_table(table_name: Annotated[str, "The table name"]) -> str:
            """Return fields of the given table."""
            try:
                return parser.get_table_fields(table_name).to_markdown()
            except Exception as exc:  # noqa: BLE001
                return f"表 {table_name} 不存在, 错误: {exc}"

        self._parse_table = parse_table

        self.thinking_agent = Agent(
            name="SQL Table Selector",
            instructions=(
                "你是一个专业的SQL专家。用户会给出一个问题, 以及数据库中的全部数据表。\n"
                "请分析哪一张表可以用来回答该问题。可以使用 parse_table 工具查看表的结构。\n"
                "如果问题需要多张表, 选择最核心的那张表, 并可以在回答中说明需要关联的其他表。\n"
                "直接输出选择的表名(例如 tracks), 不要输出其他内容。如果没有任何表能回答, 输出: 无"
            ),
            model=model,
            tools=[parse_table],
        )

    # ------------------------------------------------------------------
    async def _run_async(self, question: str) -> str:
        from agents import Runner

        result = await Runner.run(
            self.thinking_agent,
            input=(
                f"用户提问如下: {question}\n\n"
                f"可选择的表格有: {self.parser.table_names}"
            ),
        )
        return str(result.final_output)

    def run(self, question: str) -> Tuple[Optional[str], str]:
        """执行表选择, 返回 (选中的表名, 模型原始输出)

        表名解析策略:
            1. 若输出恰好是已知表名 -> 直接使用;
            2. 在输出文本中匹配已知表名 -> 使用第一个命中的表;
            3. 无法匹配 -> 返回 None, 由上层决定如何处理。
        """
        if self._has_running_loop():
            # 已在异步事件循环中运行时, asyncio.run 不可用; 提示使用异步方式
            raise RuntimeError(
                "SQLTableSelectAgent.run() 为同步封装, 不能在已运行的事件循环中调用。"
                "请在普通(非异步)上下文中调用, 或直接使用异步入口。"
            )
        raw = asyncio.run(self._run_async(question)) or ""
        raw = raw.strip()
        table = self._match_table_name(raw)
        return table, raw

    # ------------------------------------------------------------------
    def _match_table_name(self, text_: str) -> Optional[str]:
        """在模型输出中匹配已知表名"""
        for name in self.parser.table_names:
            if re.search(rf"\b{re.escape(name)}\b", text_, re.IGNORECASE):
                return name
        return None

    def _has_running_loop(self) -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False


# =================================================================================
# 3. SQL 生成 (SQLGeneratorAgent)
# =================================================================================

class SQLGeneratorAgent:
    """SQL 生成智能体: 结合 用户问题 + 选定表 的 schema/样例/外键 生成 SQL"""

    def __init__(
        self,
        parser: DBParser,
        llm: LLMClient,
        model: Optional[str] = None,
    ) -> None:
        self.parser = parser
        self.llm = llm
        self.model = model or llm.model

    def run(self, question: str, table_name: str) -> str:
        """生成 SQL, 只返回 SQL 语句本身"""
        schema_md = self.parser.get_table_schema_markdown(table_name)
        sample_md = self.parser.get_table_sample_markdown(table_name)
        relations_md = self.parser.get_relations_markdown()

        prompt = f"""你是一个专业的数据库专家。请根据用户问题与给定的数据表信息, 生成一条可直接执行的 SQL 语句。

要求:
1. 只输出 SQL 语句本身, 不要输出任何解释、markdown 代码块标记或多余文字;
2. 使用给定的表名与字段名, 不要为表名/字段名添加反引号;
3. 若问题需要跨表查询, 请根据外键关系自动使用 JOIN;
4. 若问题无法用 SQL 回答, 请只输出: NO_SQL

数据表名称: {table_name}

数据表 schema:
{schema_md}

数据表样例:
{sample_md}

数据库外键关系:
{relations_md}

用户问题: {question}
"""
        sql = self.llm.ask(prompt, model=self.model)
        return self._extract_sql(sql)

    @staticmethod
    def _extract_sql(text_: str) -> str:
        """从模型回复中抽取 SQL: 去除 markdown 代码块、开头语言标记、空白"""
        if not text_:
            return ""
        # 去掉 ```sql ... ``` 代码块
        text_ = re.sub(r"```(?:sql|SQL)?\s*(.*?)```", r"\1", text_, flags=re.DOTALL)
        text_ = text_.strip()
        # 去掉开头的 "sql" / "SQL" 标记
        text_ = re.sub(r"^(?:sql|SQL)\s*", "", text_).strip()
        # 去掉结尾多余分号以外的空白
        return text_.strip()


# =================================================================================
# 5. SQL 改写 (SQLRewriteAgent)
# =================================================================================

class SQLRewriteAgent:
    """SQL 改写智能体: 基于 原问题 + 原SQL + 报错信息 修正 SQL"""

    def __init__(
        self,
        parser: DBParser,
        llm: LLMClient,
        model: Optional[str] = None,
    ) -> None:
        self.parser = parser
        self.llm = llm
        self.model = model or llm.model

    def run(self, question: str, sql: str, error_msg: str, table_name: str) -> str:
        """根据错误信息改写 SQL, 只返回修正后的 SQL"""
        schema_md = self.parser.get_table_schema_markdown(table_name)

        prompt = f"""你是一个专业的数据库专家。上一次生成的 SQL 校验失败, 请根据错误信息修正 SQL。

要求:
1. 只输出修正后的 SQL 语句本身, 不要输出任何解释、markdown 代码块标记或多余文字;
2. 使用给定的表名与字段名, 不要为表名/字段名添加反引号;
3. 若问题无法用 SQL 回答, 请只输出: NO_SQL

用户问题: {question}

原有 SQL:
{sql}

相关数据表 schema:
{schema_md}

错误信息:
{error_msg}
"""
        fixed = self.llm.ask(prompt, model=self.model)
        return SQLGeneratorAgent._extract_sql(fixed)


# =================================================================================
# 7. 结果汇总 (SQLResultSummaryAgent)
# =================================================================================

class SQLResultSummaryAgent:
    """结果汇总智能体: 将 用户问题 + 执行SQL + 执行结果 组织为自然语言回答"""

    def __init__(
        self,
        llm: LLMClient,
        model: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.model = model or llm.model

    def run(self, question: str, sql: str, result_rows: list) -> str:
        """汇总结果为自然语言"""
        result_str = DBParser.format_result(result_rows)

        prompt = f"""你是答案汇总专家。请将用户的提问与 SQL 执行结果汇总为自然语言回答。

要求:
1. 直接输出面向用户的最终回答, 自然、准确、简洁;
2. 数据较多时归纳要点, 不要逐行罗列;
3. 不要输出 SQL 语句本身。

用户问题: {question}

执行SQL:
{sql}

执行结果:
{result_str}
"""
        return self.llm.ask(prompt, model=self.model)


def _format_result(rows: list, max_rows: int = 50) -> str:
    """将执行结果格式化为文本"""
    return DBParser.format_result(rows, max_rows=max_rows)


# =================================================================================
# SQL Agent 工作流编排
# =================================================================================

class SQLAgent:
    """SQL Agent 完整工作流

    流程: 表选择 -> SQL生成 -> (SQL校验 <-> SQL改写 循环) -> SQL执行 -> 结果汇总

    用法:
        agent = SQLAgent("sqlite:///./chinook.db", api_key="sk-xxx")
        result = agent.ask("销售额最高的前5位客户是谁？")
        print(result["answer"])
    """

    def __init__(
        self,
        db_url: str,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        select_model: str = DEFAULT_TABLE_SELECT_MODEL,
        llm_model: str = DEFAULT_LLM_MODEL,
        max_sql_retry: int = 3,
        verbose: bool = True,
        mock_mode: bool = False,
    ) -> None:
        self.db_url = db_url
        self.max_sql_retry = max_sql_retry
        self.verbose = verbose
        self.mock_mode = mock_mode

        # 1) 数据库解析
        self.parser = DBParser(db_url, verbose=verbose)

        # 2) LLM 客户端 (生成/改写/汇总)
        if mock_mode:
            # 离线模式: 不创建 LLM 客户端, 各阶段由外部(Mock)替换
            self.llm = None
            self.table_selector = None
            self.sql_generator = None
            self.sql_rewriter = None
            self.summarizer = None
            return

        self.llm = LLMClient(api_key=api_key, base_url=base_url, model=llm_model)

        # 3) 各阶段智能体
        self.table_selector = SQLTableSelectAgent(
            parser=self.parser, api_key=api_key, base_url=base_url, model=select_model
        )
        self.sql_generator = SQLGeneratorAgent(self.parser, self.llm, model=llm_model)
        self.sql_rewriter = SQLRewriteAgent(self.parser, self.llm, model=llm_model)
        self.summarizer = SQLResultSummaryAgent(self.llm, model=llm_model)

    # ------------------------------------------------------------------
    def ask(self, question: str) -> Dict[str, Any]:
        """处理用户提问, 返回完整结果(含各阶段过程记录)"""
        trace: Dict[str, Any] = {"question": question}

        # ---- 阶段2: 表选择 ----
        table, raw_select = self.table_selector.run(question)
        trace["table_select"] = {"raw": raw_select, "table": table}
        self._log(f"[表选择] 原始输出: {raw_select}")
        if table is None:
            trace["answer"] = "无法确定用于回答该问题的数据表, 请换个问法或补充数据范围。"
            trace["status"] = "no_table"
            return trace
        self._log(f"[表选择] 选中表: {table}")

        # ---- 阶段3: SQL 生成 ----
        sql = self.sql_generator.run(question, table)
        trace["sql_generated"] = sql
        self._log(f"[SQL生成] {sql}")
        if not sql or sql.upper() == "NO_SQL":
            trace["answer"] = "该问题无法用数据库 SQL 回答。"
            trace["status"] = "no_sql"
            return trace

        # ---- 阶段4+5: SQL 校验 <-> SQL 改写 (最多重试 max_sql_retry 轮) ----
        final_sql = sql
        ok, err = self.parser.check_sql(final_sql)
        rewrite_rounds: List[Dict[str, str]] = []
        while not ok and len(rewrite_rounds) < self.max_sql_retry:
            self._log(f"[SQL校验] 失败, 第{len(rewrite_rounds) + 1}次改写: {err.splitlines()[-1][:150]}")
            fixed = self.sql_rewriter.run(question, final_sql, err, table)
            rewrite_rounds.append({"error": err, "sql": final_sql, "fixed": fixed})
            if not fixed or fixed.upper() == "NO_SQL":
                break
            final_sql = fixed
            ok, err = self.parser.check_sql(final_sql)

        trace["sql_validated"] = final_sql
        trace["sql_ok"] = ok
        trace["rewrite_rounds"] = rewrite_rounds

        if not ok:
            trace["answer"] = (
                f"经过 {len(rewrite_rounds)} 次改写, SQL 仍无法通过校验。"
                f"最后一个错误: {err.splitlines()[-1][:200]}"
            )
            trace["status"] = "sql_invalid"
            return trace
        self._log(f"[SQL校验] 通过: {final_sql}")

        # ---- 阶段6: SQL 执行 ----
        rows = self.parser.execute_sql(final_sql)
        trace["rows"] = rows
        trace["row_count"] = len(rows)
        self._log(f"[SQL执行] 返回 {len(rows)} 行")

        # ---- 阶段7: 结果汇总 ----
        answer = self.summarizer.run(question, final_sql, rows)
        trace["answer"] = answer or "查询完成, 但没有得到可展示的回答。"
        trace["status"] = "ok"
        return trace

    # ------------------------------------------------------------------
    def ask_with_history(self, question: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """带历史上下文的提问(可选, 演示多轮能力, 此处保留接口)"""
        return self.ask(question)

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)


# =================================================================================
# 可选工具: 模拟生成问答数据集 (忠实移植 notebook 的"模拟生成问题"部分)
# 用于自动构建 (问题, 表, SQL, 结果, 自然语言答案) 测试数据, 便于评估 agent 效果
# =================================================================================

QUESTION_PROMPT = """你是一个专业的数据库专家，现在需要从用户的角度提问模拟生成一个提问。提问是自然语言，且计数和统计类型的问题，请直接输出具体提问，不需要有其他输出：

表名称：{table_name}

需要提问和统计的字段：{field}

表{table_name}样例如下：
{data_sample_mk}

表{table_name} schema如下：
{data_schema}
"""

ANSWER_PROMPT = """你是一个专业的数据库专家，现在需要你结合表{table_name}的信息和提问，生成对应的SQL语句。请直接输出SQL，不需要有其他输出：

表名称：{table_name}

数据表样例如下：
{data_sample_mk}

数据表schema如下：
{data_schema}

提问：{question}
"""

QUESTION_REWRITE_PROMPT = """你是一个专业的数据库专家，现在需要从用户的角度提问模拟生成一个提问。现在需要你将的下面的提问，转换为用户提问的风格。请直接输出提问，不需要有其他输出，不要直接提到表明：

原始问题：{question}

查询的表：{table_name}
"""

ANSWER_REWRITE_PROMPT = """你是一个专业的数据库专家，将下面的问题回答组织为自然语言。：

原始问题：{question}

执行SQL：{sql}

原始结果：{answer}
"""


def build_qa_dataset(
    parser: DBParser,
    llm: LLMClient,
    table_names: Optional[List[str]] = None,
    verbose: bool = True,
) -> List[List[str]]:
    """为指定表自动生成问答数据集

    返回: [[问题, 表名, SQL, 结果, 自然语言答案], ...]
    """
    from itertools import combinations

    gt_qes_answer: List[List[str]] = []

    for table_name in table_names or parser.table_names[:1]:
        data_sample = parser.get_table_sample(table_name)
        data_sample_mk = data_sample.to_markdown()
        data_schema = parser.get_table_fields(table_name).to_markdown()
        data_fields = list(data_sample.columns)

        # 待选字段: 单字段 + 两两组合 + 三三组合
        candidate_fields = list(data_fields) + list(combinations(data_fields, 2)) + list(combinations(data_fields, 3))
        candidate_fields = [" 和 ".join(x) if isinstance(x, tuple) else x for x in candidate_fields]

        for field in candidate_fields:
            for _retry in range(5):
                try:
                    # 1) 生成提问
                    question = llm.ask(QUESTION_PROMPT.format(
                        table_name=table_name, data_sample_mk=data_sample_mk,
                        data_schema=data_schema, field=field,
                    ))
                    if verbose:
                        print("question1:", question)

                    # 2) 生成答案 SQL
                    answer = llm.ask(ANSWER_PROMPT.format(
                        table_name=table_name, data_sample_mk=data_sample_mk,
                        data_schema=data_schema, question=question,
                    ))
                    answer = SQLGeneratorAgent._extract_sql(answer)

                    # 3) 校验 SQL
                    flag, _ = parser.check_sql(answer)
                    if not flag:
                        continue

                    # 4) 获取 SQL 结果 (只保留单行结果, 与 notebook 一致)
                    sql_answer = parser.execute_sql(answer)
                    if len(sql_answer) > 1:
                        continue
                    sql_answer = " ".join(str(x) for x in sql_answer[0])

                    # 5) 提问改写为更自然的用户风格
                    question = llm.ask(QUESTION_REWRITE_PROMPT.format(
                        question=question, table_name=table_name,
                    ))

                    # 6) SQL+结果 -> 自然语言答案
                    nl_answer = llm.ask(ANSWER_REWRITE_PROMPT.format(
                        question=question, sql=answer, answer=sql_answer,
                    ))

                    gt_qes_answer.append([question, table_name, answer, sql_answer, nl_answer])
                    break
                except Exception:
                    traceback.print_exc()
        break  # 与 notebook 一致, 默认只处理第一张表

    return gt_qes_answer


# =================================================================================
# 简易 main (命令行直接运行: python sql_agent.py "提问")
# =================================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python sql_agent.py \"用户提问\" [sqlite:///数据库地址]")
        sys.exit(1)

    _q = sys.argv[1]
    _db = sys.argv[2] if len(sys.argv) > 2 else "sqlite:///./chinook.db"
    _agent = SQLAgent(_db)
    _result = _agent.ask(_q)
    print("\n==================== 最终回答 ====================")
    print(_result["answer"])
