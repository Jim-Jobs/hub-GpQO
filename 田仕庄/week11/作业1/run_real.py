"""使用真实大模型运行 SQL Agent 工作流"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sql_agent import SQLAgent


def main():
    api_key = os.environ.get("GLM_API_KEY", "")
    if not api_key:
        print("未设置 GLM_API_KEY 环境变量")
        return

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinook.db")
    db_url = f"sqlite:///{db_path}"

    # 初始化 Agent（会自动调用 check_connection 验证 key）
    agent = SQLAgent(
        db_url=db_url,
        llm_api_key=api_key,
        llm_provider="glm",
    )

    # 先做一次连通性校验，便于明确判断
    ok, msg = agent.llm.check_connection()
    print()
    print("=" * 60)
    print(f"API Key 连通性校验: {'通过' if ok else '失败'}")
    print(f"  {msg[:200]}")
    print("=" * 60)
    if not ok:
        return

    questions = [
        "总共有多少首歌曲？",
        "销量最高的音乐类型是什么？",
        "哪个国家的客户最多？",
    ]

    for i, q in enumerate(questions, 1):
        print()
        print("#" * 60)
        print(f"# 问题 {i}: {q}")
        print("#" * 60)
        result = agent.query(q)

        print()
        print("--- 最终结果 ---")
        print(f"  选中表: {', '.join(result.selected_tables)}")
        print(f"  SQL: {result.sql}")
        print(f"  回答: {result.answer}")
        print(f"  成功: {'是' if result.success else '否'}")

    print()
    print("=" * 60)
    print("  真实大模型运行完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
