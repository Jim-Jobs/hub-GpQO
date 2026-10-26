# -*- coding: utf-8 -*-
"""
用「脚本化假 LLM」验证 SQLAgent 的完整七步流程，尤其是
④ 校验失败 → ⑤ 改写 → 再校验 的闭环。

运行：
    python test_sql_agent.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sql_agent_workflow import SQLAgent  # noqa: E402


class ScriptedLLM:
    """按脚本返回固定内容：第一次生成非法 SQL，随后改写为合法 SQL。"""

    def __init__(self):
        self.rewrite_calls = 0

    def select_tables(self, question, schema_summary, relations_str):
        return ["albums"]

    def generate_sql(self, question, schema_summary, relations_str, sample_md):
        # 故意返回非法 SQL：列名不存在，触发校验失败
        return "SELECT NotExistColumn FROM albums;"

    def rewrite_sql(self, question, schema_summary, relations_str, sql, error):
        self.rewrite_calls += 1
        # 改写为合法 SQL
        return "SELECT COUNT(*) AS AlbumCount FROM albums;"

    def summarize(self, question, sql, result_md):
        return f"一共有 347 张专辑。（result_md={result_md}）"


def main():
    db_url = "sqlite:///" + os.path.join(os.path.dirname(__file__), "chinook.db")
    llm = ScriptedLLM()
    agent = SQLAgent(db_url, llm=llm)

    res = agent.run("albums 表里一共有多少张专辑？")

    print("\n===== 断言校验 =====")
    assert res["sql"] == "SELECT COUNT(*) AS AlbumCount FROM albums;", "改写后的 SQL 不对"
    assert res["result"]["AlbumCount"].iloc[0] == 347, "执行结果应为 347"
    assert llm.rewrite_calls == 1, "应恰好触发一次改写"
    assert res["answer"].startswith("一共有 347"), "汇总回答不对"
    print("✓ 七步流程全部通过：选表 → 生成 → 校验失败 → 改写 → 再校验 → 执行 → 汇总")
    print("✓ 执行结果 AlbumCount =", res["result"]["AlbumCount"].iloc[0])
    print("\n完整 trace：")
    print("\n".join(res["trace"]))


if __name__ == "__main__":
    main()
