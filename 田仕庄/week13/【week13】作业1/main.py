"""SQL Agent CLI 入口。

用法：
    python main.py "每种音乐流派各有多少首歌曲？"   # 单次提问
    python main.py                                # 交互模式（REPL，支持多轮指代）

交互模式命令：
    /new   开启新会话（清空对话记忆）
    exit   退出

性能相关：回答流式输出（STREAM_ANSWER=true），并打印每个节点耗时，便于定位瓶颈。
"""

from __future__ import annotations

import sys
import time

from sql_agent import config
from sql_agent.log import get_logger

logger = get_logger("cli")

# 节点进度提示文案
_NODE_LABELS = {
    "get_schema": "分析问题、匹配相关表结构",
    "write_query": "生成 SQL",
    "optimize_query": "专业化改写 SQL",
    "review_query": "评审 SQL 质量",
    "execute_query": "安全校验并执行",
    "rewrite_query": "根据错误修正 SQL（自动重试）",
    "generate_answer": "组织自然语言回答",
}


class AnswerPrinter:
    """回答流式打印器：首 token 立即输出，降低感知延迟。"""

    #: 流式开始前先打印自己的进度标签（保证"标签 → 回答"的阅读顺序）
    NODE_LABEL = "  ⏳ generate_answer: 组织自然语言回答"

    def __init__(self) -> None:
        self._printed = False

    def reset(self) -> None:
        self._printed = False

    def feed(self, token: str) -> None:
        if not self._printed:
            print(f"{self.NODE_LABEL}")
            print("\n🤖 ", end="", flush=True)
            self._printed = True
        print(token, end="", flush=True)

    @property
    def printed(self) -> bool:
        return self._printed

    def finish(self) -> None:
        if self._printed:
            print()  # 流式输出结尾换行


def _print_sql_and_result(state: dict) -> None:
    """节点执行完立即展示 SQL 与查询结果（先于回答，保持阅读顺序）。"""
    sql = state.get("sql")
    if not sql:
        return
    print(f"\n  [SQL]\n  {sql.replace(chr(10), chr(10) + '  ')}")
    if state.get("error"):
        print(f"  [错误] {state['error']}")
    elif state.get("query_result"):
        print(f"\n  [结果]\n  {state['query_result'].replace(chr(10), chr(10) + '  ')}")


def run_once(agent, question: str, thread_id: str = "cli",
             printer: AnswerPrinter | None = None) -> dict:
    """执行一次问答：stream 逐步展示节点流转与耗时，回答流式输出。"""
    print(f"\n你: {question}")
    logger.info("ask: %s (thread=%s)", question, thread_id)

    if printer:
        printer.reset()
    final: dict = {}
    timings: list[tuple[str, float]] = []

    t_start = time.perf_counter()
    t_prev = t_start
    for update in agent.stream(
        {"question": question},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        now = time.perf_counter()
        for node, partial in update.items():
            if node in _NODE_LABELS and partial is not None:
                dt = now - t_prev
                timings.append((node, dt))
                # generate_answer 的标签由流式打印机在首 token 时输出，避免顺序错位
                if not (node == "generate_answer" and printer and printer.printed):
                    print(f"  ⏳ {node}: {_NODE_LABELS[node]}  ({dt:.1f}s)")
                final.update(partial)
                if node == "execute_query":
                    _print_sql_and_result(final)
        t_prev = now
    total = time.perf_counter() - t_start

    # 流式已输出则只补换行；否则（失败兜底/未开启流式）整段打印
    if printer and printer.printed:
        printer.finish()
    else:
        print(f"\n🤖 {final.get('answer', '')}")

    logger.info("done: %.2fs, 节点耗时 %s", total,
                ", ".join(f"{n}={d:.2f}s" for n, d in timings))
    breakdown = " · ".join(f"{n} {d:.1f}s" for n, d in timings)
    print(f"\n  ⏱  总耗时 {total:.2f}s  |  {breakdown}")
    print("─" * 70)
    final["_elapsed"] = total
    return final


def main() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from sql_agent.graph import build_agent
    from sql_agent.llm import describe_llm
    from sql_agent.log import setup_logging

    setup_logging()
    printer = AnswerPrinter()
    agent = build_agent(
        checkpointer=MemorySaver(),  # 内存会话记忆，支持多轮指代
        on_token=printer.feed if config.STREAM_ANSWER else None,
    )
    logger.info("启动: %s, 评审回路=%s, 改写策略=%s, 流式=%s",
                describe_llm(), config.ENABLE_QUERY_REVIEW,
                config.OPTIMIZE_STRATEGY, config.STREAM_ANSWER)

    # 单次提问模式
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        run_once(agent, question, printer=printer)
        return

    # 交互模式（REPL）
    session_no = 1
    print("=" * 70)
    print("  SQL Agent（LangGraph + Chinook）")
    print('  输入自然语言问题进行查询；支持多轮对话（如"那前10名呢？"）')
    print("  命令：/new 开新会话 | exit 退出")
    print("=" * 70)
    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q", "退出"}:
            print("再见！")
            break
        if question == "/new":
            session_no += 1
            print(f"✨ 已开启新会话 #{session_no}（对话记忆已清空）")
            continue
        try:
            run_once(agent, question, thread_id=f"cli-{session_no}", printer=printer)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 处理出错: {e}")


if __name__ == "__main__":
    main()
