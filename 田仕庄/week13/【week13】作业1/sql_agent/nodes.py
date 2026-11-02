"""LangGraph 图节点实现。

每个节点都是 AgentState -> partial(AgentState) 的纯函数工厂，
依赖（LLM、DBParser）通过工厂参数注入，便于测试时替换为 Fake 实现。
"""

from __future__ import annotations

import json
import re
from typing import Callable

from sql_agent import config
from sql_agent.db_parser import DBParser, SQLSafetyError
from sql_agent.log import get_logger
from sql_agent.state import AgentState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 表名 ↔ 中文/英文概念 映射（用于问题到表的粗匹配）
# ---------------------------------------------------------------------------
TABLE_KEYWORDS: dict[str, list[str]] = {
    "artists":       ["艺术家", "歌手", "乐队", "artist", "singer", "band"],
    "albums":        ["专辑", "唱片", "album"],
    "tracks":        ["歌曲", "曲目", "音乐", "track", "song", "music"],
    "genres":        ["流派", "类型", "风格", "genre"],
    "customers":     ["客户", "顾客", "用户", "消费者", "customer", "client", "user"],
    "invoices":      ["订单", "发票", "购买", "消费", "销量", "销售额", "营收",
                      "invoice", "order", "sale", "revenue"],
    "invoice_items": ["订单明细", "订单项", "明细", "单价", "数量", "item", "quantity", "unit"],
    "employees":     ["员工", "雇员", "职员", "employee", "staff"],
    "playlists":     ["播放列表", "歌单", "playlist"],
    "playlist_track": ["播放列表歌曲", "歌单歌曲"],
    "media_types":   ["媒体类型", "格式", "media", "format"],
    # 通用兜底概念
    "customers|invoices|tracks": ["国家", "地区", "country", "city", "城市",
                                   "最多", "最高", "前几", "排名", "top", "best"],
}

WRITE_QUERY_SYSTEM = """你是一名 SQLite 专家。根据用户问题和数据库结构，编写一条**只读** SQL 查询。

规则：
1. 只能输出一条以 SELECT 或 WITH 开头的查询语句，禁止任何写操作（INSERT/UPDATE/DELETE/DROP等）。
2. 仅使用给定结构中存在的表和字段，注意利用外键关系做 JOIN。
3. 除非用户明确要求全部，否则对结果做合理限制（如 ORDER BY + LIMIT）。
4. 用户问题可能包含指代（如"那前10名呢？"），请结合对话历史理解真实意图。
5. 只输出 SQL 本身，不要任何解释。用 ```sql ``` 代码块包裹。"""

REWRITE_QUERY_SYSTEM = """你是一名 SQLite 专家。之前生成的 SQL 执行失败了。
请根据错误信息修正 SQL。

规则：
1. 逐字核对：SQL 中引用的每个 表.列 必须严格存在于上面的表结构中。
   常见错误：跨表 JOIN 直接引用不存在的列（如 A 表没有 B 表的外键列），
   此时需要经中间表中转关联（如 invoice_items 只有 TrackId，关联 albums 必须经 tracks 表）。
2. 如果之前已修正过但仍失败，**禁止输出与之前相同或等价的 SQL**，
   请换一种查询思路重新分析表结构与 JOIN 路径。
3. 只能输出一条以 SELECT 或 WITH 开头的只读查询语句。
4. 只输出修正后的 SQL 本身，不要任何解释。用 ```sql ``` 代码块包裹。"""

OPTIMIZE_QUERY_SYSTEM = """你是一名资深 SQL 工程师。请对给定 SQL 进行专业化改写，要求**保持语义完全一致**（回答同一个问题）：

1. 修正表选择 / JOIN / 聚合口径问题（如有）；
2. 规范化：列名明确无歧义、ORDER BY 合理、按问题意图补充合理的 LIMIT；
3. 注意 JOIN 可能导致行数翻倍，必要时使用 DISTINCT 或聚合前先去重；
4. 只能输出一条以 SELECT 或 WITH 开头的只读语句。

若原 SQL 已足够专业、无需改动，就原样输出。
只输出改写后的 SQL 本身，不要任何解释。用 ```sql ``` 代码块包裹。"""

REVIEW_QUERY_SYSTEM = """你是严格的 SQL 评审专家。根据用户问题与数据库结构，按以下 checklist 评审 SQL：

1. 表选择：是否查询了正确的表（如"销量/消费"应基于 invoices/invoice_items，而非 tracks）；
2. 聚合口径：COUNT/SUM/AVG 的对象是否符合问题意图；
3. JOIN 条件：外键关联是否正确，是否会因 JOIN 导致行数翻倍；
4. 结果限制：是否有 ORDER BY 与合理的 LIMIT；
5. 只读安全：必须是单条 SELECT/WITH 语句。

输出严格的 JSON（不要 markdown 代码块、不要其他内容）：
{"verdict": "approve 或 revise", "feedback": "若 revise，说明具体问题与修改方向；approve 时为空串"}"""

GENERATE_ANSWER_SYSTEM = """你是一名数据分析助手。请根据用户问题、执行的 SQL 和查询结果，
用简洁自然的中文回答用户问题。

要求：
1. 直接回答问题本身，给出具体数字和结论。
2. 如有多行数据，提炼要点（Top 项、总量、趋势），不要逐行罗列超过 10 行。
3. 不要提及你收到了 SQL 或表格，就像直接回答用户一样。
4. 你是**只读查询助手**，无法修改、删除或写入任何数据。如果用户的意图是写操作
   （删除/修改/插入数据），请明确告知用户你只支持查询，并说明实际返回的是相关只读信息。"""


# ---------------------------------------------------------------------------
# SQL 提取工具
# ---------------------------------------------------------------------------
def extract_sql(text: str) -> str:
    """从 LLM 输出中提取 SQL（容忍 ```sql 代码块或裸文本）。"""
    text = text.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


def history_block(history: list, max_turns: int = 3) -> str:
    """把最近几轮 (问题, 回答) 渲染为提示词片段；无历史返回空串。"""
    if not history:
        return ""
    lines = ["\n\n对话历史（最近几轮，供理解指代）："]
    for q, a in history[-max_turns:]:
        lines.append(f"用户：{q}")
        lines.append(f"助手：{a}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extract_verdict(text: str) -> dict:
    """从评审 LLM 输出中解析 JSON 结论；解析失败按 approve 处理（不阻塞流程）。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if obj.get("verdict") in ("approve", "revise"):
                return {"verdict": obj["verdict"],
                        "feedback": str(obj.get("feedback", ""))}
        except (json.JSONDecodeError, AttributeError):
            pass
    # 宽松降级：按关键词判断
    low = text.lower()
    if "revise" in low:
        return {"verdict": "revise", "feedback": text.strip()}
    return {"verdict": "approve", "feedback": ""}


# ---------------------------------------------------------------------------
# 节点工厂
# ---------------------------------------------------------------------------
def make_get_schema(parser: DBParser) -> Callable:
    """节点 get_schema：问题 → 相关表结构上下文。"""

    def get_schema(state: AgentState) -> dict:
        question = state["question"].lower()
        matched: list[str] = []
        for table, keywords in TABLE_KEYWORDS.items():
            if any(kw.lower() in question for kw in keywords):
                # 支持组合 key（如 "customers|invoices|tracks"）
                for t in table.split("|"):
                    if t in parser.table_names and t not in matched:
                        matched.append(t)

        if not matched:
            # 未命中任何关键词 → 使用全部表（库不大，全量上下文可接受）
            matched = list(parser.table_names)
            logger.info("get_schema: 未命中关键词，使用全部表")
        else:
            # 扩充：命中表通过外键直接关联的表也纳入上下文
            related = set(matched)
            for fk in parser.foreign_keys:
                if fk["constrained_table"] in matched:
                    related.add(fk["referred_table"])
                if fk["referred_table"] in matched:
                    related.add(fk["constrained_table"])
            matched = [t for t in parser.table_names if t in related]

        logger.info("get_schema: 命中表 %s", matched)
        return {
            "table_names": matched,
            "table_schemas": parser.schema_markdown(
                matched, sample_rows=config.SCHEMA_SAMPLE_ROWS
            ),
            "retry_count": 0,
            "error": "",
            "optimize_count": 0,
            "review_verdict": "",
            "review_feedback": "",
        }

    return get_schema


def make_write_query(llm: Callable) -> Callable:
    """节点 write_query：问题 + 表结构 → SQL。"""

    def write_query(state: AgentState) -> dict:
        prompt = (
            f"数据库结构如下：\n\n{state['table_schemas']}\n\n"
            f"{history_block(state.get('history'))}\n"
            f"用户问题：{state['question']}"
        )
        resp = llm.invoke([
            ("system", WRITE_QUERY_SYSTEM), ("human", prompt),
        ])
        sql = extract_sql(resp.content)
        logger.info("write_query:\n%s", sql)
        return {"sql": sql, "original_sql": sql}

    return write_query


def make_execute_query(parser: DBParser) -> Callable:
    """节点 execute_query：安全校验 + 执行 SQL。"""

    def execute_query(state: AgentState) -> dict:
        sql = state["sql"]
        try:
            md = parser.result_to_markdown(sql)
            logger.info("execute_query: 执行成功，结果 %d 字符", len(md))
            logger.debug("execute_query: 结果预览: %s", md[:200])
            return {"query_result": md, "error": ""}
        except SQLSafetyError as e:
            logger.warning("execute_query: 安全校验未通过 - %s", e)
            return _maybe_fallback(parser, state, sql,
                                   f"SQL 安全校验未通过: {e}")
        except Exception as e:  # noqa: BLE001 SQL 执行错误回传给重写节点
            logger.warning("execute_query: 执行失败 - %s", e)
            return _maybe_fallback(parser, state, sql, f"SQL 执行错误: {e}")

    return execute_query


def _maybe_fallback(parser: DBParser, state: AgentState,
                    sql: str, error: str) -> dict:
    """改写后的 SQL 执行失败时，回退执行 write_query 的原 SQL（一次性）。"""
    original = state.get("original_sql") or ""
    if original.strip() and original.strip() != sql.strip():
        try:
            md = parser.result_to_markdown(original)
            logger.warning("execute_query: 改写 SQL 失败，回退原 SQL 成功")
            return {"query_result": md, "error": "", "sql": original}
        except Exception as e:  # noqa: BLE001 原 SQL 也失败，走正常重写回路
            logger.warning("execute_query: 原 SQL 回退也失败 - %s", e)
            error = f"{error}；原 SQL 回退也失败: {e}"
    return {"query_result": "", "error": error}


def make_optimize_query(llm: Callable) -> Callable:
    """节点 optimize_query：专业化改写 SQL（Reflection 模式的执行者）。

    首轮在 write_query 之后；revise 轮携带评审意见再次进入。
    """
    def optimize_query(state: AgentState) -> dict:
        count = state.get("optimize_count", 0) + 1
        feedback = state.get("review_feedback") or ""
        prompt = (
            f"数据库结构如下：\n\n{state['table_schemas']}\n\n"
            f"{history_block(state.get('history'))}\n"
            f"用户问题：{state['question']}\n\n"
            f"待改写的 SQL：\n```sql\n{state['sql']}\n```"
        )
        if feedback:
            prompt += (
                f"\n\n评审意见（第 {count - 1} 轮评审未通过，请针对性修正）：\n{feedback}"
            )
        resp = llm.invoke([
            ("system", OPTIMIZE_QUERY_SYSTEM), ("human", prompt),
        ])
        sql = extract_sql(resp.content)
        logger.info("optimize_query(第%d轮):\n%s", count, sql)
        return {"sql": sql, "optimize_count": count}

    return optimize_query


def make_review_query(llm: Callable) -> Callable:
    """节点 review_query：按 checklist 评审 SQL（语义正确性把关）。"""

    def review_query(state: AgentState) -> dict:
        prompt = (
            f"数据库结构如下：\n\n{state['table_schemas']}\n\n"
            f"用户问题：{state['question']}\n\n"
            f"待评审 SQL：\n```sql\n{state['sql']}\n```"
        )
        resp = llm.invoke([
            ("system", REVIEW_QUERY_SYSTEM), ("human", prompt),
        ])
        verdict = extract_verdict(resp.content)
        logger.info("review_query: %s %s", verdict["verdict"],
                    f"| {verdict['feedback'][:80]}" if verdict["feedback"] else "")
        return {"review_verdict": verdict["verdict"],
                "review_feedback": verdict["feedback"]}

    return review_query


def make_rewrite_query(llm: Callable) -> Callable:
    """节点 rewrite_query：错误反馈 → 修正 SQL（重试回路）。"""

    def rewrite_query(state: AgentState) -> dict:
        retry = state.get("retry_count", 0) + 1
        prompt = (
            f"数据库结构如下：\n\n{state['table_schemas']}\n\n"
            f"{history_block(state.get('history'))}\n"
            f"用户问题：{state['question']}\n\n"
            f"上一次的 SQL：\n```sql\n{state['sql']}\n```\n\n"
            f"错误信息：{state['error']}\n\n"
            f"这是第 {retry} 次修正（最多 {config.MAX_RETRIES} 次）。"
        )
        resp = llm.invoke([
            ("system", REWRITE_QUERY_SYSTEM), ("human", prompt),
        ])
        sql = extract_sql(resp.content)
        logger.info("rewrite_query(第%d次):\n%s", retry, sql)
        return {"sql": sql, "retry_count": retry}

    return rewrite_query


def make_generate_answer(llm: Callable, on_token: Callable[[str], None] | None = None
                         ) -> Callable:
    """节点 generate_answer：查询结果 → 自然语言回答（含失败兜底）。

    参数 on_token：传入回调时改用流式生成，边生成边回调（CLI 实时打印，降低感知延迟）。
    流式不可用（如 Fake LLM）或中途失败时自动回退为一次性 invoke。
    """

    def _invoke(messages) -> str:
        """一次性生成（无回调或流式不可用时的路径）。"""
        return llm.invoke(messages).content.strip()

    def _stream(messages) -> str:
        """流式生成：逐 token 回调，返回完整文本。"""
        if on_token is None or not hasattr(llm, "stream"):
            return _invoke(messages)
        parts: list[str] = []
        try:
            for chunk in llm.stream(messages):
                piece = chunk.content if isinstance(chunk.content, str) else ""
                if piece:
                    parts.append(piece)
                    on_token(piece)
        except Exception as e:  # noqa: BLE001 网络中断等：已输出的部分仍然可用
            logger.warning("generate_answer: 流式中断（已输出 %d 字符）- %s",
                           sum(len(p) for p in parts), e)
            if not parts:
                return _invoke(messages)
        return "".join(parts).strip()

    def generate_answer(state: AgentState) -> dict:
        # 重试耗尽仍失败：给出友好的失败说明（不调用 LLM）
        if state.get("error"):
            answer = (
                "很抱歉，我尝试了多次仍未能生成可正确执行的 SQL 查询。\n\n"
                f"最后一次失败原因：{state['error']}\n\n"
                "请尝试换一种更明确的问法，或直接告诉我你想查询哪张表的信息。"
            )
            return {"answer": answer, "history": [(state["question"], answer)]}

        prompt = (
            f"{history_block(state.get('history'))}\n"
            f"用户问题：{state['question']}\n\n"
            f"执行的 SQL：\n```sql\n{state['sql']}\n```\n\n"
            f"查询结果：\n{state['query_result']}"
        )
        messages = [("system", GENERATE_ANSWER_SYSTEM), ("human", prompt)]
        answer = _stream(messages)
        logger.info("generate_answer: 完成，回答 %d 字", len(answer))
        # 追加 (问题, 回答) 到会话记忆（history 为 reducer 累加字段）
        return {"answer": answer, "history": [(state["question"], answer)]}

    return generate_answer
