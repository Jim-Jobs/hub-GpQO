"""
SQL Agent 完整演示 - 非交互式运行
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sql_agent import DBParser, SQLValidator
from demo import MockSQLAgent


def main():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinook.db")
    db_url = f"sqlite:///{db_path}"

    print("=" * 60)
    print("  SQL Agent 完整工作流演示")
    print("=" * 60)
    print()
    print("模式: Mock（模拟 LLM，无需 API Key）")
    print()

    # ============================================================
    # 演示 1: 数据库解析
    # ============================================================
    print("=" * 60)
    print("【演示 1】数据库解析 (DBParser)")
    print("=" * 60)

    parser = DBParser(db_url)
    print()
    print("解析完成")
    print("  - 表数量:", len(parser.tables))
    print("  - 外键关系:", len(parser.foreign_keys))
    print()
    print("表列表:")
    for tname, tinfo in parser.tables.items():
        print(f"  * {tname} ({tinfo.row_count} 行, {len(tinfo.columns)} 字段)")

    print()
    print("表间关系 (外键):")
    for fk in parser.foreign_keys:
        ct = fk["constrained_table"]
        cc = fk["constrained_columns"][0]
        rt = fk["referred_table"]
        rc = fk["referred_columns"][0]
        print(f"  * {ct}.{cc} -> {rt}.{rc}")

    # ============================================================
    # 演示 2: SQL 校验
    # ============================================================
    print()
    print("=" * 60)
    print("【演示 2】SQL 校验 (SQLValidator)")
    print("=" * 60)

    validator = SQLValidator(parser)

    test_cases = [
        ("SELECT * FROM tracks", "正确的 SQL"),
        ("SELECT * FROM nonexistent_table", "不存在的表"),
        ("DROP TABLE tracks", "非 SELECT 语句"),
        ("SELECT COUNT(*) FROM tracks", "聚合查询"),
    ]

    for sql, desc in test_cases:
        is_valid, err_msg, details = validator.validate(sql)
        status = "[PASS]" if is_valid else "[FAIL]"
        print()
        print(f"  {desc} {status}")
        print(f"    SQL: {sql}")
        if not is_valid:
            # 去除换行，截断显示
            err_short = err_msg.replace("\n", " ")[:70]
            print(f"    错误: {err_short}...")

    # ============================================================
    # 演示 3: SQL 执行
    # ============================================================
    print()
    print("=" * 60)
    print("【演示 3】SQL 执行")
    print("=" * 60)

    test_sqls = [
        "SELECT COUNT(*) as total FROM tracks",
        "SELECT Name, Composer FROM tracks LIMIT 3",
        ("SELECT g.Name as genre, COUNT(*) as cnt "
         "FROM tracks t JOIN genres g ON t.GenreId = g.GenreId "
         "GROUP BY g.GenreId ORDER BY cnt DESC LIMIT 5"),
    ]

    for i, sql in enumerate(test_sqls, 1):
        print()
        print(f"  查询 {i}: {sql}")
        ok, result, cnt = parser.execute_sql(sql)
        if ok:
            print(f"    [OK] 成功，返回 {cnt} 行")
            print(f"    列: {', '.join(result['columns'])}")
            for row in result["rows"][:3]:
                vals = ", ".join(f"{k}={v}" for k, v in row.items())
                print(f"    {vals}")
            if result["info"]:
                print(f"    注意: {result['info']}")
        else:
            err = str(result).replace("\n", " ")[:60]
            print(f"    [FAIL] 失败: {err}...")

    # ============================================================
    # 演示 4: 完整工作流
    # ============================================================
    print()
    print("=" * 60)
    print("【演示 4】完整 SQL Agent 工作流")
    print("=" * 60)

    agent = MockSQLAgent(db_url)

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
        print("--- 执行步骤汇总 ---")
        total_time = 0
        for s in result.steps:
            icon = "OK" if s.status == "success" else "FAIL"
            print(f"  [{icon}] {s.step}: {s.duration:.2f}s")
            total_time += s.duration
        print(f"  总耗时: {total_time:.2f}s")

        print()
        print("--- 最终结果 ---")
        print(f"  选中表: {', '.join(result.selected_tables)}")
        print(f"  SQL: {result.sql}")
        print(f"  回答: {result.answer}")
        print(f"  成功: {'是' if result.success else '否'}")

    print()
    print("=" * 60)
    print("  演示完成！")
    print("=" * 60)
    print()
    print("使用真实 LLM 的方式:")
    print("  $env:GLM_API_KEY=\"your_key\"")
    print("  python demo.py")


if __name__ == "__main__":
    main()
