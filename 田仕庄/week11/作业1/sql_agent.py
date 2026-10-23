"""
SQL Agent - 完整的自然语言转 SQL 工作流
========================================
流程：用户提问 → 数据库解析 → Table 选择 → SQL 生成 → SQL 校验 → SQL 改写 → SQL 执行 → 结果汇总
"""

import time
import jwt
import requests
import traceback
import re
import warnings
from typing import List, Dict, Tuple, Optional, Union
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import create_engine, inspect, func, select, Table, MetaData, text
from sqlalchemy.exc import SQLAlchemyError


# ============================================================
# 1. LLM 适配器
# ============================================================
# 常见服务商默认地址
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
OPENAI_BASE_URL = "https://api.openai.com/v1/chat/completions"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 服务商预设：provider -> (默认 base_url, 默认 model)
PROVIDER_PRESETS = {
    "glm": (GLM_BASE_URL, "glm-3-turbo"),
    "openai": (OPENAI_BASE_URL, "gpt-4o-mini"),
    "deepseek": (DEEPSEEK_BASE_URL, "deepseek-chat"),
    "dashscope": (DASHSCOPE_BASE_URL, "qwen-plus"),
}


class LLMClient:
    """大语言模型客户端

    支持两类认证方式：
    - GLM（智谱）：使用 id.secret 格式的 key 生成 JWT
    - OpenAI 兼容 API（DeepSeek / Qwen 等）：直接使用 Bearer <api_key>

    provider="auto" 时，根据 base_url 自动判断认证方式。
    """

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "", provider: str = "auto"):
        self.api_key = api_key

        # 解析服务商预设
        preset_url, preset_model = PROVIDER_PRESETS.get(provider, ("", ""))
        self.base_url = base_url or preset_url or GLM_BASE_URL

        if provider == "auto":
            # 根据 base_url 判断认证方式
            self.provider = "glm" if "bigmodel.cn" in self.base_url else "openai"
        else:
            self.provider = provider

        self.model = model or preset_model or "glm-3-turbo"

    def _generate_token(self, exp_seconds: int = 1000) -> str:
        """生成 GLM API 的 JWT token（仅 GLM 使用）"""
        try:
            id_part, secret = self.api_key.split(".")
        except Exception as e:
            raise ValueError(
                f"无效的 GLM API key 格式（应为 id.secret 形式）: {e}"
            )

        payload = {
            "api_key": id_part,
            "exp": int(round(time.time() * 1000)) + exp_seconds * 1000,
            "timestamp": int(round(time.time() * 1000)),
        }
        return self._encode_token(payload, secret)

    @staticmethod
    def _encode_token(payload: dict, secret: str) -> str:
        """编码 JWT，屏蔽 PyJWT 关于密钥长度的无害告警"""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return jwt.encode(
                payload,
                secret,
                algorithm="HS256",
                headers={"alg": "HS256", "sign_type": "SIGN"},
            )

    def _build_headers(self) -> Dict[str, str]:
        """按服务商构建请求头"""
        if self.provider == "glm":
            return {
                "Content-Type": "application/json",
                "Authorization": self._generate_token(),
            }
        # OpenAI 兼容 API：直接 Bearer
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def ask(self, question: str, nretry: int = 3, temperature: float = 0.5) -> Optional[str]:
        """向 LLM 提问，返回回答文本"""
        if not self.api_key:
            return None

        if nretry == 0:
            return None

        try:
            headers = self._build_headers()
        except Exception as e:
            print(f"  [LLM 认证失败]: {e}")
            return None

        data = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "user", "content": question}],
        }
        try:
            response = requests.post(self.base_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  [LLM 请求失败，剩余重试 {nretry - 1} 次]: {e}")
            return self.ask(question, nretry - 1, temperature)

    def check_connection(self) -> Tuple[bool, str]:
        """快速校验 API Key 是否可用，返回 (是否可用, 说明)"""
        if not self.api_key:
            return False, "未配置 API Key"

        try:
            headers = self._build_headers()
        except Exception as e:
            return False, str(e)

        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
        }
        try:
            r = requests.post(self.base_url, headers=headers, json=data, timeout=20)
            if r.status_code == 200:
                return True, "连接正常"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


# ============================================================
# 2. 数据库解析器 (DBParser)
# ============================================================
@dataclass
class ColumnInfo:
    """字段信息"""
    name: str
    type: str
    nullable: bool
    default: any
    primary_key: int
    distinct: int = 0
    nan_count: int = 0
    max_val: any = None
    min_val: any = None
    random_values: List[str] = field(default_factory=list)
    mode: any = None  # 众数（文本字段）


@dataclass
class TableInfo:
    """表信息"""
    name: str
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)
    sample: pd.DataFrame = None
    row_count: int = 0


class DBParser:
    """数据库解析器 - 自动扫描数据库结构、字段统计、表关系"""

    def __init__(self, db_url: str):
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

        self.tables: Dict[str, TableInfo] = {}
        self.foreign_keys: List[Dict] = []

        self._parse_all_tables()

    def _parse_all_tables(self):
        """解析所有表的结构和统计信息"""
        metadata = MetaData()

        for table_name in self.table_names:
            print(f"  解析表: {table_name}")
            table_info = TableInfo(name=table_name)

            # 外键信息
            fks = self.inspector.get_foreign_keys(table_name)
            for fk in fks:
                self.foreign_keys.append({
                    "constrained_table": table_name,
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

            # 字段信息
            table_instance = Table(table_name, metadata, autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)

            # 行数统计
            count_query = select(func.count()).select_from(table_instance)
            row_count = self.conn.execute(count_query).fetchone()[0]
            table_info.row_count = row_count

            for col_meta in table_columns:
                col_name = col_meta["name"]
                col_instance = getattr(table_instance.columns, col_name)

                col_info = ColumnInfo(
                    name=col_name,
                    type=str(col_meta["type"]),
                    nullable=col_meta.get("nullable", True),
                    default=col_meta.get("default", None),
                    primary_key=col_meta.get("primary_key", 0),
                )

                # distinct 计数
                try:
                    query = select(func.count(func.distinct(col_instance)))
                    col_info.distinct = self.conn.execute(query).fetchone()[0]
                except Exception:
                    pass

                # 空值计数
                try:
                    query = select(func.count()).filter(col_instance.is_(None))
                    col_info.nan_count = self.conn.execute(query).fetchone()[0]
                except Exception:
                    pass

                # max / min
                try:
                    col_info.max_val = self.conn.execute(select(func.max(col_instance))).fetchone()[0]
                    col_info.min_val = self.conn.execute(select(func.min(col_instance))).fetchone()[0]
                except Exception:
                    pass

                # 随机采样值
                try:
                    query = select(col_instance).distinct().limit(10)
                    random_vals = [str(row[0]) for row in self.conn.execute(query).all() if row[0] is not None]
                    col_info.random_values = list(set(random_vals))[:5]
                except Exception:
                    pass

                # 文本字段的众数
                field_type_lower = col_info.type.lower()
                if "text" in field_type_lower or "char" in field_type_lower or "varchar" in field_type_lower:
                    try:
                        query = (
                            select(col_instance, func.count().label("cnt"))
                            .group_by(col_instance)
                            .order_by(func.count().desc())
                            .limit(1)
                        )
                        result = self.conn.execute(query).fetchone()
                        if result:
                            col_info.mode = result[0]
                    except Exception:
                        pass

                table_info.columns[col_name] = col_info

            # 表样例（前 3 行）
            try:
                query = select(table_instance).limit(3)
                rows = self.conn.execute(query).fetchall()
                if rows:
                    table_info.sample = pd.DataFrame([dict(row._mapping) for row in rows])
            except Exception:
                pass

            self.tables[table_name] = table_info

    def get_table_fields_df(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息（DataFrame 格式）"""
        if table_name not in self.tables:
            return pd.DataFrame()
        t = self.tables[table_name]
        data = []
        for col in t.columns.values():
            data.append({
                "字段名": col.name,
                "类型": col.type,
                "可空": col.nullable,
                "主键": col.primary_key,
                "去重数": col.distinct,
                "空值数": col.nan_count,
                "最大值": col.max_val,
                "最小值": col.min_val,
                "样例值": ", ".join(col.random_values[:3]) if col.random_values else "",
            })
        return pd.DataFrame(data)

    def get_relations_df(self) -> pd.DataFrame:
        """获取表间关系（外键）"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取表样例数据"""
        if table_name in self.tables:
            return self.tables[table_name].sample
        return pd.DataFrame()

    def get_table_schema_text(self, table_name: str) -> str:
        """获取表的 schema 描述文本（给 LLM 用）"""
        if table_name not in self.tables:
            return ""
        t = self.tables[table_name]
        lines = [f"表名: {table_name} (共 {t.row_count} 行)"]
        lines.append("字段:")
        for col in t.columns.values():
            pk_mark = " [主键]" if col.primary_key else ""
            samples = f"，样例: {', '.join(col.random_values[:3])}" if col.random_values else ""
            lines.append(f"  - {col.name} ({col.type}){pk_mark}{samples}")
        return "\n".join(lines)

    def check_sql(self, sql: str) -> Tuple[bool, str]:
        """检查 SQL 是否可以执行（仅校验，不真正执行数据修改）
        返回: (是否合法, 错误信息或 'ok')
        """
        try:
            # 使用 explain 来验证 SQL 语法，不实际执行
            if self.db_type == "sqlite":
                validate_sql = f"EXPLAIN QUERY PLAN {sql}"
            else:
                validate_sql = f"EXPLAIN {sql}"
            self.conn.execute(text(validate_sql))
            return True, "ok"
        except Exception as e:
            err_msg = str(e)
            return False, err_msg

    def execute_sql(self, sql: str, limit: int = 100) -> Tuple[bool, Union[List, str], int]:
        """执行 SQL，返回 (是否成功, 结果或错误信息, 行数)
        自动加 LIMIT 保护，防止返回过多数据
        """
        try:
            # 安全检查：只允许 SELECT 语句
            sql_stripped = sql.strip().rstrip(";").strip()
            if not sql_stripped.upper().startswith("SELECT"):
                return False, "只允许执行 SELECT 查询语句", 0

            # 自动加 LIMIT 保护
            if "LIMIT" not in sql_stripped.upper():
                sql_to_run = f"{sql_stripped} LIMIT {limit + 1}"
            else:
                sql_to_run = sql_stripped

            result = self.conn.execute(text(sql_to_run))
            rows = result.fetchall()
            columns = list(result.keys())

            # 转换为字典列表
            data = [dict(zip(columns, row)) for row in rows]
            row_count = len(data)

            # 提示是否被截断
            if row_count > limit:
                data = data[:limit]
                info = f"结果已截断，仅显示前 {limit} 行（实际更多）"
            else:
                info = ""

            return True, {"rows": data, "columns": columns, "info": info}, row_count

        except Exception as e:
            err_msg = traceback.format_exc()
            return False, err_msg, 0


# ============================================================
# 3. Table 选择器
# ============================================================
TABLE_SELECT_PROMPT = """你是一个专业的数据库专家。根据用户的问题，从以下表列表中选择最相关的表。

【所有可用表】
{all_tables}

【表间关系（外键）】
{relations}

【用户问题】
{question}

请直接输出表名列表，用英文逗号分隔，不要输出其他内容。只选择与问题直接相关的表。
"""


class TableSelector:
    """Table 选择器 - 根据用户问题选择相关的表"""

    def __init__(self, db_parser: DBParser, llm: LLMClient):
        self.db = db_parser
        self.llm = llm

    def select(self, question: str) -> List[str]:
        """选择相关的表"""
        # 构建所有表的简要描述
        table_descs = []
        for tname, tinfo in self.db.tables.items():
            col_names = ", ".join(list(tinfo.columns.keys())[:8])
            table_descs.append(f"- {tname}: {col_names} ({tinfo.row_count} 行)")

        all_tables_text = "\n".join(table_descs)

        # 构建关系描述
        relations = self.db.get_relations_df()
        if not relations.empty:
            rel_lines = []
            for _, row in relations.iterrows():
                rel_lines.append(
                    f"- {row['constrained_table']}.{row['constrained_columns'][0]} "
                    f"→ {row['referred_table']}.{row['referred_columns'][0]}"
                )
            relations_text = "\n".join(rel_lines)
        else:
            relations_text = "无外键关系"

        prompt = TABLE_SELECT_PROMPT.format(
            all_tables=all_tables_text,
            relations=relations_text,
            question=question,
        )

        response = self.llm.ask(prompt, temperature=0.3)

        if response:
            # 解析表名
            tables = [t.strip() for t in re.split(r"[,，\n]", response) if t.strip()]
            # 过滤掉不存在的表
            valid_tables = [t for t in tables if t in self.db.tables]
            if valid_tables:
                # 自动补充关联表（如果选中的表之间有外键关系，确保中间表都被包含）
                valid_tables = self._expand_related_tables(valid_tables)
                return valid_tables

        # 兜底：返回所有表
        return list(self.db.tables.keys())

    def _expand_related_tables(self, selected_tables: List[str]) -> List[str]:
        """如果选中的多张表之间有关联路径，补充中间关联表"""
        result = set(selected_tables)

        # 查找选中表之间的直接外键关系
        for fk in self.db.foreign_keys:
            constrained = fk["constrained_table"]
            referred = fk["referred_table"]
            # 如果两端都在选中集合中，或者一端在另一端是关联表
            if constrained in result and referred not in result:
                # 检查 referred 是否是连接其他选中表的桥梁
                pass  # 简单起见，暂不自动扩展，避免引入不相关表

        return list(result)


# ============================================================
# 4. SQL 生成器
# ============================================================
SQL_GENERATE_PROMPT = """你是一个专业的 SQL 专家。根据用户的问题和数据库表信息，生成正确的 SQL 查询语句。

【相关表信息】
{table_schemas}

【表间关系】
{relations}

【用户问题】
{question}

【要求】
1. 只输出 SQL 语句，不要有任何其他解释、不要用 markdown 代码块
2. 使用标准 SQL 语法，兼容 SQLite
3. 表名和字段名必须严格使用上面提供的名称
4. 如果需要关联多张表，使用正确的 JOIN 和 ON 条件
5. 只生成 SELECT 查询，不要生成 INSERT/UPDATE/DELETE
6. 如果问题涉及计数、统计，使用 COUNT/SUM/AVG 等聚合函数
7. 适当使用 LIMIT 限制返回行数（除非问题明确要求全部数据）

直接输出 SQL：
"""


class SQLGenerator:
    """SQL 生成器 - 根据自然语言问题和表信息生成 SQL"""

    def __init__(self, db_parser: DBParser, llm: LLMClient):
        self.db = db_parser
        self.llm = llm

    def generate(self, question: str, selected_tables: List[str]) -> str:
        """生成 SQL"""
        # 构建表 schema 信息
        schema_parts = []
        for tname in selected_tables:
            schema_parts.append(self.db.get_table_schema_text(tname))
        table_schemas = "\n\n".join(schema_parts)

        # 构建相关表的外键关系
        relevant_relations = []
        for fk in self.db.foreign_keys:
            if fk["constrained_table"] in selected_tables or fk["referred_table"] in selected_tables:
                relevant_relations.append(
                    f"- {fk['constrained_table']}.{fk['constrained_columns'][0]} "
                    f"→ {fk['referred_table']}.{fk['referred_columns'][0]}"
                )
        relations_text = "\n".join(relevant_relations) if relevant_relations else "无"

        prompt = SQL_GENERATE_PROMPT.format(
            table_schemas=table_schemas,
            relations=relations_text,
            question=question,
        )

        response = self.llm.ask(prompt, temperature=0.2)
        if response:
            return self._clean_sql(response)
        return ""

    @staticmethod
    def _clean_sql(sql: str) -> str:
        """清理 SQL 文本，去除 markdown 代码块等"""
        sql = sql.strip()
        # 去除 ```sql ... ``` 包裹
        if sql.startswith("```"):
            sql = re.sub(r"^```(?:sql)?\s*", "", sql)
            sql = re.sub(r"\s*```$", "", sql)
        # 去除前后的空白和分号
        sql = sql.strip().rstrip(";").strip()
        return sql


# ============================================================
# 5. SQL 校验器
# ============================================================
class SQLValidator:
    """SQL 校验器 - 检查 SQL 的语法正确性和语义合理性"""

    def __init__(self, db_parser: DBParser):
        self.db = db_parser

    def validate(self, sql: str) -> Tuple[bool, str, Dict]:
        """校验 SQL
        返回: (是否通过, 错误信息, 详细校验结果)
        """
        results = {
            "syntax_ok": False,
            "tables_exist": True,
            "columns_exist": True,
            "is_select": True,
            "issues": [],
        }

        # 检查是否是 SELECT
        sql_stripped = sql.strip()
        if not sql_stripped.upper().startswith("SELECT"):
            results["is_select"] = False
            results["issues"].append("不是 SELECT 查询语句")
            return False, "只允许 SELECT 查询", results

        # 语法校验（通过 EXPLAIN）
        syntax_ok, err_msg = self.db.check_sql(sql)
        results["syntax_ok"] = syntax_ok
        if not syntax_ok:
            results["issues"].append(f"语法错误: {err_msg}")
            return False, err_msg, results

        # 检查表名是否存在（简单正则提取）
        tables_in_sql = self._extract_tables(sql)
        for t in tables_in_sql:
            if t not in self.db.tables:
                results["tables_exist"] = False
                results["issues"].append(f"表不存在: {t}")

        if not results["tables_exist"]:
            return False, "引用了不存在的表", results

        return True, "ok", results

    @staticmethod
    def _extract_tables(sql: str) -> List[str]:
        """从 SQL 中提取表名（简单实现）"""
        # 匹配 FROM 和 JOIN 后的表名
        pattern = r"(?:FROM|JOIN|INTO|UPDATE)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        matches = re.findall(pattern, sql, re.IGNORECASE)
        return list(set(matches))


# ============================================================
# 6. SQL 改写器
# ============================================================
SQL_REWRITE_PROMPT = """你是一个专业的 SQL 优化专家。下面的 SQL 有错误或可以优化，请根据表信息进行修正。

【相关表信息】
{table_schemas}

【表间关系】
{relations}

【原始 SQL】
{sql}

【错误信息】
{error_msg}

【用户原始问题】
{question}

【要求】
1. 修正 SQL 中的错误，确保可以正确执行
2. 保持查询逻辑与用户问题一致
3. 只输出修正后的 SQL，不要有其他解释
4. 不要用 markdown 代码块包裹

修正后的 SQL：
"""


class SQLRewriter:
    """SQL 改写器 - 修复错误 SQL，优化 SQL"""

    def __init__(self, db_parser: DBParser, llm: LLMClient):
        self.db = db_parser
        self.llm = llm

    def rewrite(self, sql: str, error_msg: str, question: str,
                selected_tables: List[str]) -> str:
        """根据错误信息改写 SQL"""
        # 构建表 schema
        schema_parts = []
        for tname in selected_tables:
            schema_parts.append(self.db.get_table_schema_text(tname))
        table_schemas = "\n\n".join(schema_parts)

        # 构建关系
        relevant_relations = []
        for fk in self.db.foreign_keys:
            if fk["constrained_table"] in selected_tables or fk["referred_table"] in selected_tables:
                relevant_relations.append(
                    f"- {fk['constrained_table']}.{fk['constrained_columns'][0]} "
                    f"→ {fk['referred_table']}.{fk['referred_columns'][0]}"
                )
        relations_text = "\n".join(relevant_relations) if relevant_relations else "无"

        prompt = SQL_REWRITE_PROMPT.format(
            table_schemas=table_schemas,
            relations=relations_text,
            sql=sql,
            error_msg=error_msg,
            question=question,
        )

        response = self.llm.ask(prompt, temperature=0.3)
        if response:
            return SQLGenerator._clean_sql(response)
        return sql

    def try_fix(self, sql: str, error_msg: str, question: str,
                selected_tables: List[str], max_attempts: int = 3) -> Tuple[str, bool]:
        """尝试多次修复 SQL
        返回: (最终 SQL, 是否修复成功)
        """
        current_sql = sql
        current_error = error_msg

        for attempt in range(max_attempts):
            print(f"  [SQL 改写] 第 {attempt + 1} 次尝试...")
            new_sql = self.rewrite(current_sql, current_error, question, selected_tables)
            if not new_sql or new_sql == current_sql:
                # 没有变化，无法修复
                break

            # 校验新 SQL
            ok, err = self.db.check_sql(new_sql)
            if ok:
                print(f"  [SQL 改写] 修复成功！")
                return new_sql, True

            current_sql = new_sql
            current_error = err

        return current_sql, False


# ============================================================
# 7. 结果汇总器
# ============================================================
SUMMARIZE_PROMPT = """你是一个数据分析助手。根据用户的问题、SQL 查询和查询结果，用自然语言给出清晰的回答。

【用户问题】
{question}

【执行的 SQL】
{sql}

【查询结果】
{result_text}

【要求】
1. 用自然语言回答用户的问题，不要直接贴原始数据
2. 如果结果是数字，直接给出数字并说明含义
3. 如果结果是列表，总结关键信息
4. 回答要简洁、准确、易懂
5. 如果结果为空，明确告知用户没有找到相关数据

回答：
"""


class ResultSummarizer:
    """结果汇总器 - 将 SQL 查询结果转换为自然语言回答"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def summarize(self, question: str, sql: str,
                  result_data: Dict) -> str:
        """将查询结果汇总为自然语言"""
        rows = result_data.get("rows", [])
        columns = result_data.get("columns", [])
        info = result_data.get("info", "")

        if not rows:
            return "没有找到相关数据。"

        # 构建结果文本
        result_lines = []
        result_lines.append(f"共 {len(rows)} 条结果")
        if columns:
            result_lines.append("列: " + ", ".join(columns))
        result_lines.append("")

        # 格式化结果（最多显示 20 行）
        display_rows = rows[:20]
        for i, row in enumerate(display_rows, 1):
            row_str = " | ".join(f"{k}: {v}" for k, v in row.items())
            result_lines.append(f"{i}. {row_str}")

        if len(rows) > 20:
            result_lines.append(f"... 还有 {len(rows) - 20} 条未显示")

        if info:
            result_lines.append(f"\n注意: {info}")

        result_text = "\n".join(result_lines)

        # 调用 LLM 汇总
        prompt = SUMMARIZE_PROMPT.format(
            question=question,
            sql=sql,
            result_text=result_text,
        )

        response = self.llm.ask(prompt, temperature=0.4)
        if response:
            return response.strip()

        # 兜底：直接返回格式化结果
        return result_text


# ============================================================
# 8. SQL Agent 主编排器
# ============================================================
@dataclass
class AgentStepLog:
    """单步执行日志"""
    step: str
    status: str  # success / failed / skipped
    detail: str = ""
    duration: float = 0.0


@dataclass
class AgentResult:
    """Agent 执行结果"""
    question: str
    answer: str
    sql: str
    selected_tables: List[str]
    result_data: Dict
    steps: List[AgentStepLog] = field(default_factory=list)
    success: bool = False


class SQLAgent:
    """SQL Agent - 完整的自然语言转 SQL 工作流编排器"""

    def __init__(self, db_url: str, llm_api_key: str = "",
                 llm_model: str = "", llm_provider: str = "auto",
                 llm_base_url: str = ""):
        """初始化 Agent

        参数：
        - llm_provider: "glm" / "openai" / "deepseek" / "dashscope" / "auto"
        - llm_base_url: 自定义接口地址（留空则用 provider 预设）
        - llm_model:    留空则用 provider 预设模型
        """
        print("=" * 60)
        print("SQL Agent 初始化...")
        print("=" * 60)

        # 初始化 LLM
        self.llm = LLMClient(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
            provider=llm_provider,
        )
        print(f"\n[步骤 0-1] LLM 配置: provider={self.llm.provider}, "
              f"model={self.llm.model}")

        # 校验 API Key 是否可用（仅在有 key 时执行，避免默认进入 Mock 场景报错）
        if llm_api_key:
            ok, msg = self.llm.check_connection()
            if ok:
                print("  ✓ API Key 校验通过，将使用真实大模型")
            else:
                print("  ✗ API Key 校验失败，无法使用真实大模型：")
                print(f"    {msg}")
                print("    → 请更换有效的 API Key，或改用 Mock 模式（不传 llm_api_key）")
        else:
            print("  ⚠ 未配置 API Key，将进入 Mock 模式（仅用于流程演示）")

        # 初始化数据库解析器
        print("\n[步骤 0-2] 数据库解析中...")
        t0 = time.time()
        self.db = DBParser(db_url)
        print(f"  解析完成，共 {len(self.db.tables)} 张表，{len(self.db.foreign_keys)} 个外键关系")
        print(f"  耗时: {time.time() - t0:.2f}s")

        # 初始化各模块
        self.table_selector = TableSelector(self.db, self.llm)
        self.sql_generator = SQLGenerator(self.db, self.llm)
        self.sql_validator = SQLValidator(self.db)
        self.sql_rewriter = SQLRewriter(self.db, self.llm)
        self.summarizer = ResultSummarizer(self.llm)

        print("\n" + "=" * 60)
        print("SQL Agent 初始化完成！")
        print("=" * 60)

    def query(self, question: str, max_rewrite_attempts: int = 3) -> AgentResult:
        """执行完整的 SQL Agent 工作流"""
        result = AgentResult(question=question, answer="", sql="",
                             selected_tables=[], result_data={})

        print(f"\n{'='*60}")
        print(f"用户问题: {question}")
        print(f"{'='*60}")

        # ----------------------------------------------------------
        # 步骤 1: Table 选择
        # ----------------------------------------------------------
        print(f"\n[步骤 1] Table 选择...")
        t0 = time.time()
        try:
            selected_tables = self.table_selector.select(question)
            result.selected_tables = selected_tables
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="Table 选择",
                status="success",
                detail=f"选中 {len(selected_tables)} 张表: {', '.join(selected_tables)}",
                duration=step_time,
            ))
            print(f"  选中的表: {', '.join(selected_tables)}")
            print(f"  耗时: {step_time:.2f}s")
        except Exception as e:
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="Table 选择", status="failed",
                detail=str(e), duration=step_time,
            ))
            print(f"  失败: {e}")
            selected_tables = list(self.db.tables.keys())
            result.selected_tables = selected_tables

        # ----------------------------------------------------------
        # 步骤 2: SQL 生成
        # ----------------------------------------------------------
        print(f"\n[步骤 2] SQL 生成...")
        t0 = time.time()
        try:
            sql = self.sql_generator.generate(question, selected_tables)
            result.sql = sql
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="SQL 生成",
                status="success",
                detail=sql[:100] + "..." if len(sql) > 100 else sql,
                duration=step_time,
            ))
            print(f"  生成的 SQL: {sql}")
            print(f"  耗时: {step_time:.2f}s")
        except Exception as e:
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="SQL 生成", status="failed",
                detail=str(e), duration=step_time,
            ))
            print(f"  失败: {e}")
            result.answer = f"SQL 生成失败: {e}"
            return result

        if not sql:
            result.answer = "未能生成有效的 SQL"
            result.steps.append(AgentStepLog(
                step="SQL 生成", status="failed", detail="生成的 SQL 为空",
            ))
            return result

        # ----------------------------------------------------------
        # 步骤 3: SQL 校验
        # ----------------------------------------------------------
        print(f"\n[步骤 3] SQL 校验...")
        t0 = time.time()
        is_valid, error_msg, validate_details = self.sql_validator.validate(sql)
        step_time = time.time() - t0

        if is_valid:
            result.steps.append(AgentStepLog(
                step="SQL 校验", status="success",
                detail="SQL 语法正确", duration=step_time,
            ))
            print(f"  ✓ SQL 校验通过")
            print(f"  耗时: {step_time:.2f}s")
        else:
            result.steps.append(AgentStepLog(
                step="SQL 校验", status="failed",
                detail=error_msg, duration=step_time,
            ))
            print(f"  ✗ SQL 校验失败: {error_msg}")

            # ------------------------------------------------------
            # 步骤 4: SQL 改写（修复错误）
            # ------------------------------------------------------
            print(f"\n[步骤 4] SQL 改写（修复错误）...")
            t0 = time.time()
            try:
                fixed_sql, fix_success = self.sql_rewriter.try_fix(
                    sql, error_msg, question, selected_tables,
                    max_attempts=max_rewrite_attempts,
                )
                step_time = time.time() - t0

                if fix_success:
                    result.sql = fixed_sql
                    sql = fixed_sql
                    result.steps.append(AgentStepLog(
                        step="SQL 改写", status="success",
                        detail=f"修复后: {fixed_sql[:100]}...",
                        duration=step_time,
                    ))
                    print(f"  ✓ SQL 改写成功: {fixed_sql}")
                else:
                    result.steps.append(AgentStepLog(
                        step="SQL 改写", status="failed",
                        detail=f"经过 {max_rewrite_attempts} 次尝试仍无法修复",
                        duration=step_time,
                    ))
                    print(f"  ✗ SQL 改写失败，无法修复")
                    result.answer = f"SQL 生成有误且无法自动修复: {error_msg}"
                    return result
            except Exception as e:
                step_time = time.time() - t0
                result.steps.append(AgentStepLog(
                    step="SQL 改写", status="failed",
                    detail=str(e), duration=step_time,
                ))
                print(f"  ✗ SQL 改写异常: {e}")
                result.answer = f"SQL 改写过程出错: {e}"
                return result

        # ----------------------------------------------------------
        # 步骤 5: SQL 执行
        # ----------------------------------------------------------
        print(f"\n[步骤 5] SQL 执行...")
        t0 = time.time()
        exec_success, exec_result, row_count = self.db.execute_sql(sql)
        step_time = time.time() - t0

        if exec_success:
            result.result_data = exec_result
            result.steps.append(AgentStepLog(
                step="SQL 执行", status="success",
                detail=f"返回 {row_count} 行数据",
                duration=step_time,
            ))
            print(f"  ✓ 执行成功，返回 {row_count} 行")
            print(f"  耗时: {step_time:.2f}s")
        else:
            result.steps.append(AgentStepLog(
                step="SQL 执行", status="failed",
                detail=str(exec_result), duration=step_time,
            ))
            print(f"  ✗ 执行失败: {exec_result}")

            # 执行失败，再尝试一次改写
            print(f"\n[步骤 5.1] 执行失败，尝试 SQL 改写修复...")
            fixed_sql, fix_success = self.sql_rewriter.try_fix(
                sql, str(exec_result), question, selected_tables,
                max_attempts=max_rewrite_attempts,
            )
            if fix_success:
                print(f"  重试执行: {fixed_sql}")
                exec_success, exec_result, row_count = self.db.execute_sql(fixed_sql)
                if exec_success:
                    result.sql = fixed_sql
                    result.result_data = exec_result
                    result.steps.append(AgentStepLog(
                        step="SQL 改写(执行后)", status="success",
                        detail=f"修复后执行成功", duration=0,
                    ))
                    print(f"  ✓ 修复后执行成功")
                else:
                    result.answer = f"SQL 执行失败，且修复后仍失败: {exec_result}"
                    return result
            else:
                result.answer = f"SQL 执行失败: {exec_result}"
                return result

        # ----------------------------------------------------------
        # 步骤 6: 结果汇总
        # ----------------------------------------------------------
        print(f"\n[步骤 6] 结果汇总...")
        t0 = time.time()
        try:
            summary = self.summarizer.summarize(question, sql, exec_result)
            result.answer = summary
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="结果汇总", status="success",
                detail=summary[:100] + "..." if len(summary) > 100 else summary,
                duration=step_time,
            ))
            print(f"  汇总完成")
            print(f"  耗时: {step_time:.2f}s")
        except Exception as e:
            step_time = time.time() - t0
            result.steps.append(AgentStepLog(
                step="结果汇总", status="failed",
                detail=str(e), duration=step_time,
            ))
            print(f"  失败: {e}")
            # 兜底：直接返回原始结果
            rows = exec_result.get("rows", []) if isinstance(exec_result, dict) else []
            result.answer = f"查询结果（共 {len(rows)} 行）: {str(rows[:5])}"

        result.success = True

        # 打印最终答案
        print(f"\n{'='*60}")
        print(f"最终回答:")
        print(f"{result.answer}")
        print(f"{'='*60}")

        return result

    def print_steps(self, result: AgentResult):
        """打印执行步骤详情"""
        print(f"\n{'='*60}")
        print("执行步骤详情")
        print(f"{'='*60}")
        total_time = 0
        for step in result.steps:
            status_icon = "✓" if step.status == "success" else "✗"
            print(f"  {status_icon} {step.step}: {step.status} ({step.duration:.2f}s)")
            if step.detail:
                print(f"      {step.detail[:80]}")
            total_time += step.duration
        print(f"\n  总耗时: {total_time:.2f}s")
        print(f"{'='*60}")


# ============================================================
# 主入口（演示用）
# ============================================================
if __name__ == "__main__":
    import os

    # 数据库路径
    db_path = os.path.join(os.path.dirname(__file__), "chinook.db")
    db_url = f"sqlite:///{db_path}"

    # API Key（从环境变量获取，或直接填写）
    api_key = os.environ.get("GLM_API_KEY", "")

    # 初始化 Agent
    agent = SQLAgent(db_url=db_url, llm_api_key=api_key)

    # 示例问题
    questions = [
        "总共有多少首歌曲？",
        "销量最高的音乐类型是什么？",
        "哪个国家的客户最多？",
    ]

    for q in questions:
        result = agent.query(q)
        agent.print_steps(result)
        print("\n\n")
