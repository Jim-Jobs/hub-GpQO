"""LangGraph AgentState 定义。"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    """SQL Agent 全流程共享状态。

    节点只读取需要的字段、只覆写自己负责的字段。
    """
    # 输入
    question: str          # 用户自然语言问题

    # 会话记忆：generate_answer 每轮追加 (问题, 回答)，
    # 配合 checkpointer + thread_id 跨轮次保留，支持"那前10名呢？"这类指代
    history: Annotated[list, operator.add]

    # get_schema 产出
    table_names: list      # 命中的相关表名
    table_schemas: str     # 拼装后的表结构/样例上下文（Markdown）

    # write_query / optimize_query 产出
    sql: str               # 当前生成的 SQL
    original_sql: str      # write_query 的原始 SQL（改写失败时回退执行）
    retry_count: int       # 执行失败已重试次数

    # 评审回路（Reflection 模式）产出
    optimize_count: int    # 改写已执行轮次（revise 回路上限）
    review_verdict: str    # 评审结论：approve / revise（空串表示未启用评审）
    review_feedback: str   # 评审意见（revise 时回传给改写节点）

    # execute_query 产出
    query_result: str      # 查询结果（Markdown 表格）或错误描述
    error: str             # 最近一次执行错误（空串表示成功）

    # generate_answer 产出
    answer: str            # 最终自然语言回答
