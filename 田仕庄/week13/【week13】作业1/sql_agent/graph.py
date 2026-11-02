"""LangGraph StateGraph 组装：SQL Agent 主流程。

```
START → get_schema → write_query → review_query
review_query --approve--> execute_query --成功--> generate_answer → END
review_query --revise且未达上限--> optimize_query → review_query（≤MAX_OPTIMIZE_ROUNDS 轮）
execute_query --失败--> rewrite_query → execute_query（≤MAX_RETRIES 次）
```
`OPTIMIZE_STRATEGY=always` 时无条件先改写（write → optimize → review），兼容旧行为。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph

from sql_agent import config
from sql_agent.db_parser import DBParser
from sql_agent.llm import get_fast_llm, get_llm
from sql_agent.log import get_logger
from sql_agent.nodes import (
    make_execute_query,
    make_generate_answer,
    make_get_schema,
    make_optimize_query,
    make_review_query,
    make_rewrite_query,
    make_write_query,
)
from sql_agent.state import AgentState

logger = get_logger(__name__)


def _route_after_execute(state: AgentState) -> str:
    """条件边：执行成功 → 生成回答；失败且可重试 → 重写 SQL；否则失败收尾。"""
    if not state.get("error"):
        logger.info("route: execute_query -> generate_answer (执行成功)")
        return "generate_answer"
    if state.get("retry_count", 0) < config.MAX_RETRIES:
        logger.info(
            "route: execute_query -> rewrite_query (第 %d/%d 次重试, 错误: %s)",
            state.get("retry_count", 0) + 1, config.MAX_RETRIES,
            str(state.get("error"))[:100],
        )
        return "rewrite_query"
    logger.warning("route: execute_query -> generate_answer (重试耗尽，失败收尾)")
    return "generate_answer"


def _route_after_review(state: AgentState) -> str:
    """条件边：评审通过 → 执行；不通过且未达上限 → 带意见回到改写；否则强制放行。"""
    if state.get("review_verdict") == "approve":
        logger.info("route: review_query -> execute_query (评审通过)")
        return "execute_query"
    if state.get("optimize_count", 0) < config.MAX_OPTIMIZE_ROUNDS:
        logger.info(
            "route: review_query -> optimize_query (revise, 第 %d/%d 轮改写)",
            state.get("optimize_count", 0) + 1, config.MAX_OPTIMIZE_ROUNDS,
        )
        return "optimize_query"
    logger.warning("route: review_query -> execute_query (revise 轮次耗尽，强制放行)")
    return "execute_query"


def build_agent(llm=None, parser: Optional[DBParser] = None,
                compiled: bool = True, checkpointer=None,
                fast_llm=None, on_token: Optional[Callable[[str], None]] = None):
    """构建 SQL Agent 图。

    参数：
        llm:    LLM 客户端（需实现 invoke(messages)），缺省用真实 LLM（测试可注入 Fake）
        parser: DBParser 实例，缺省新建
        compiled: True 返回编译后的图（invoke 可用），False 返回原始 builder（测试断言结构用）
        checkpointer: LangGraph checkpointer（如 MemorySaver）；传入后配合
                      config={"configurable": {"thread_id": ...}} 实现多轮会话记忆
        fast_llm: 辅助节点（改写/评审/回答）用 LLM；注入 llm 时默认复用同一个（测试友好）
        on_token: 回答流式回调，传入则 generate_answer 边生成边输出（降低感知延迟）

    拓扑（config.ENABLE_QUERY_REVIEW=true 时启用 Reflection 回路）：
        START → get_schema → write_query → review_query（OPTIMIZE_STRATEGY=always 时先过 optimize_query）
        review_query --approve--> execute_query --成功--> generate_answer → END
        review_query --revise且未达上限--> optimize_query → review_query（≤MAX_OPTIMIZE_ROUNDS 轮）
        execute_query --失败--> rewrite_query → execute_query（≤MAX_RETRIES 次）
    """
    main_llm = llm or get_llm()
    # 未注入时用辅助模型（可与主模型不同）；注入 Fake 时复用同一实例，避免测试触网
    aux_llm = fast_llm or llm or get_fast_llm()
    parser = parser or DBParser()

    builder = StateGraph(AgentState)
    builder.add_node("get_schema", make_get_schema(parser))
    builder.add_node("write_query", make_write_query(main_llm))
    builder.add_node("execute_query", make_execute_query(parser))
    builder.add_node("rewrite_query", make_rewrite_query(main_llm))
    builder.add_node("generate_answer", make_generate_answer(aux_llm, on_token))

    builder.add_edge(START, "get_schema")
    builder.add_edge("get_schema", "write_query")

    if config.ENABLE_QUERY_REVIEW:
        # Reflection 模式：评审把关 + 按需改写
        builder.add_node("optimize_query", make_optimize_query(aux_llm))
        builder.add_node("review_query", make_review_query(aux_llm))
        builder.add_edge("optimize_query", "review_query")
        if config.OPTIMIZE_STRATEGY == "always":
            # 兼容旧行为：无条件先改写再评审（每次多一次 LLM 调用）
            builder.add_edge("write_query", "optimize_query")
        else:
            # 默认 on_revise：先评审，只有不通过才改写（happy path 省一次调用）
            builder.add_edge("write_query", "review_query")
        builder.add_conditional_edges(
            "review_query",
            _route_after_review,
            {
                "execute_query": "execute_query",
                "optimize_query": "optimize_query",
            },
        )
    else:
        builder.add_edge("write_query", "execute_query")
    builder.add_conditional_edges(
        "execute_query",
        _route_after_execute,
        {
            "generate_answer": "generate_answer",
            "rewrite_query": "rewrite_query",
        },
    )
    builder.add_edge("rewrite_query", "execute_query")
    builder.add_edge("generate_answer", END)

    logger.debug("build_agent: 图构建完成 (llm=%s, aux=%s, parser=%s, 策略=%s)",
                 type(main_llm).__name__, type(aux_llm).__name__,
                 type(parser).__name__, config.OPTIMIZE_STRATEGY)
    return builder.compile(checkpointer=checkpointer) if compiled else builder


@lru_cache(maxsize=1)
def _default_agent():
    """进程级单例：默认 Agent（真实 LLM + 默认 DBParser）。"""
    return build_agent()


def ask(question: str) -> dict:
    """快捷入口：向默认 Agent 提问，返回最终 state。"""
    return _default_agent().invoke({"question": question})
