"""性能基准：逐节点测量 SQL Agent 的端到端耗时。

用法：
    python tools/bench_perf.py              # 默认题库
    python tools/bench_perf.py "你的问题"    # 自定义问题

输出：每个节点的耗时（由 agent.stream 的 update 间隔推算）+ 总耗时 + LLM 调用次数。
用于优化前后对比。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 允许从任意目录以 `python tools/bench_perf.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sql_agent import config  # noqa: E402
from sql_agent.db_parser import DBParser  # noqa: E402
from sql_agent.graph import build_agent  # noqa: E402
from sql_agent.llm import get_llm  # noqa: E402
from sql_agent.log import setup_logging  # noqa: E402

QUESTIONS = [
    # 简单：单表聚合
    "每种音乐流派各有多少首歌曲？",
    # 中等：两表 JOIN + 分组排序
    "哪个国家的客户消费总额最高？前3名",
    # 困难：四表 JOIN（语义易错）
    "销量最高的前3位艺术家是谁？（按订单金额算）",
]


class CountingLLM:
    """包装真实 LLM，统计调用次数与累计耗时。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.seconds = 0.0

    def invoke(self, messages, *args, **kwargs):
        self.calls += 1
        t0 = time.perf_counter()
        try:
            return self._inner.invoke(messages, *args, **kwargs)
        finally:
            self.seconds += time.perf_counter() - t0

    def __getattr__(self, name):
        return getattr(self._inner, name)


def bench_one(agent, llm: CountingLLM, question: str) -> float:
    print(f"\n{'=' * 72}\n问题：{question}")
    print(f"模型：{config.LLM_MODEL}  评审回路：{config.ENABLE_QUERY_REVIEW}  "
          f"改写策略：{config.OPTIMIZE_STRATEGY}  思维链：{config.LLM_THINKING}")
    print("-" * 72)

    t_start = time.perf_counter()
    calls_before = llm.calls
    t_prev = t_start
    final: dict = {}
    for update in agent.stream(
        {"question": question},
        stream_mode="updates",
        config={"configurable": {"thread_id": "bench"}},
    ):
        now = time.perf_counter()
        for node, partial in update.items():
            print(f"  {node:<18} {now - t_prev:6.2f}s")
            if partial:
                final.update(partial)
        t_prev = now

    total = time.perf_counter() - t_start
    print("-" * 72)
    answer = (final.get("answer") or "").replace("\n", " ")[:160]
    print(f"  总计 {total:6.2f}s | 本问 LLM 调用 {llm.calls - calls_before} 次 | 答案：{answer}")
    return total


def main() -> None:
    setup_logging(level="WARNING")
    questions = sys.argv[1:] or QUESTIONS

    parser = DBParser()
    llm = CountingLLM(get_llm())
    agent = build_agent(llm=llm, parser=parser)

    results = []
    for q in questions:
        before_calls = llm.calls
        t = bench_one(agent, llm, q)
        results.append((q, t, llm.calls - before_calls))

    print(f"\n{'=' * 72}\n汇总")
    print("-" * 72)
    for q, t, n in results:
        print(f"  {t:6.2f}s | {n} 次调用 | {q}")
    avg = sum(r[1] for r in results) / len(results)
    print("-" * 72)
    print(f"  平均 {avg:.2f}s/问，总 LLM 耗时占比 "
          f"{llm.seconds / sum(r[1] for r in results) * 100:.0f}%")


if __name__ == "__main__":
    main()
