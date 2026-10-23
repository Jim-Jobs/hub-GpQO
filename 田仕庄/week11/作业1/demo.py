"""
SQL Agent 演示脚本
==================
演示完整的 SQL Agent 工作流：
1. 数据库解析 → 2. Table 选择 → 3. SQL 生成 → 4. SQL 校验 → 5. SQL 改写 → 6. SQL 执行 → 7. 结果汇总

支持两种模式：
- Mock 模式：使用内置的模拟 LLM 响应，无需 API Key 即可演示完整流程
- 真实模式：使用 GLM API，需要设置 GLM_API_KEY 环境变量
"""

import os
import sys
import time
import re
from typing import List, Dict, Optional

# 把当前目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sql_agent import (
    DBParser, SQLValidator, SQLRewriter, ResultSummarizer,
    TableSelector, SQLGenerator, SQLAgent, AgentResult, AgentStepLog,
    LLMClient,
)


# ============================================================
# Mock LLM - 模拟大模型，用于无 API Key 时的演示
# ============================================================
class MockLLM(LLMClient):
    """模拟 LLM，根据预设规则返回响应，用于演示"""

    def __init__(self):
        super().__init__(api_key="mock", model="mock")
        # 预设的问答对（问题关键词 → 对应 SQL）
        self.sql_qa_pairs = {
            "多少首歌曲": "SELECT COUNT(*) as total_tracks FROM tracks",
            "歌曲总数": "SELECT COUNT(*) as total_tracks FROM tracks",
            "总共有多少首": "SELECT COUNT(*) as total_tracks FROM tracks",
            "销量最高的音乐类型": """
                SELECT g.Name as genre_name, COUNT(*) as sale_count, SUM(ii.UnitPrice * ii.Quantity) as total_revenue
                FROM genres g
                JOIN tracks t ON g.GenreId = t.GenreId
                JOIN invoice_items ii ON t.TrackId = ii.TrackId
                GROUP BY g.GenreId
                ORDER BY sale_count DESC
                LIMIT 1
            """.strip(),
            "最受欢迎的音乐类型": """
                SELECT g.Name as genre_name, COUNT(*) as sale_count
                FROM genres g
                JOIN tracks t ON g.GenreId = t.GenreId
                JOIN invoice_items ii ON t.TrackId = ii.TrackId
                GROUP BY g.GenreId
                ORDER BY sale_count DESC
                LIMIT 1
            """.strip(),
            "哪个国家的客户最多": """
                SELECT Country, COUNT(*) as customer_count
                FROM customers
                GROUP BY Country
                ORDER BY customer_count DESC
                LIMIT 1
            """.strip(),
            "客户最多的国家": """
                SELECT Country, COUNT(*) as customer_count
                FROM customers
                GROUP BY Country
                ORDER BY customer_count DESC
                LIMIT 1
            """.strip(),
            "总销售额": """
                SELECT SUM(Total) as total_sales FROM invoices
            """.strip(),
            "销售总额": """
                SELECT SUM(Total) as total_sales FROM invoices
            """.strip(),
            "多少客户": "SELECT COUNT(*) as total_customers FROM customers",
            "客户总数": "SELECT COUNT(*) as total_customers FROM customers",
            "多少张专辑": "SELECT COUNT(*) as total_albums FROM albums",
            "专辑数量": "SELECT COUNT(*) as total_albums FROM albums",
            "收入最高的艺术家": """
                SELECT a.Name as artist_name, SUM(ii.UnitPrice * ii.Quantity) as total_revenue
                FROM artists a
                JOIN albums al ON a.ArtistId = al.ArtistId
                JOIN tracks t ON al.AlbumId = t.AlbumId
                JOIN invoice_items ii ON t.TrackId = ii.TrackId
                GROUP BY a.ArtistId
                ORDER BY total_revenue DESC
                LIMIT 1
            """.strip(),
            "最长的歌曲": """
                SELECT Name, Milliseconds / 1000 as seconds, Composer
                FROM tracks
                ORDER BY Milliseconds DESC
                LIMIT 5
            """.strip(),
            "员工有多少": "SELECT COUNT(*) as total_employees FROM employees",
            "播放列表数量": "SELECT COUNT(*) as total_playlists FROM playlists",
        }

        # 预设的 table 选择（问题关键词 → 相关表）
        self.table_qa_pairs = {
            "歌曲": ["tracks", "albums", "artists", "genres"],
            "音乐类型": ["genres", "tracks"],
            "销量": ["invoices", "invoice_items", "tracks"],
            "客户": ["customers"],
            "国家": ["customers"],
            "专辑": ["albums", "artists"],
            "艺术家": ["artists", "albums"],
            "员工": ["employees"],
            "播放列表": ["playlists", "playlist_track", "tracks"],
            "销售": ["invoices", "invoice_items"],
            "收入": ["invoices", "invoice_items", "tracks"],
        }

    def ask(self, question: str, nretry: int = 3, temperature: float = 0.5) -> Optional[str]:
        """模拟 LLM 回答"""
        time.sleep(0.3)  # 模拟网络延迟

        q = question.strip()

        # 判断是哪种 prompt 类型
        if "选择最相关的表" in q:
            return self._mock_table_select(q)
        elif "生成正确的 SQL" in q or "直接输出 SQL" in q:
            return self._mock_sql_generate(q)
        elif "修正" in q and "SQL" in q:
            return self._mock_sql_rewrite(q)
        elif "自然语言给出清晰的回答" in q or "用自然语言回答" in q:
            return self._mock_summarize(q)
        else:
            return "Mock LLM: 未识别的 prompt 类型"

    def _mock_table_select(self, question: str) -> str:
        """模拟 Table 选择"""
        selected = set()
        for keyword, tables in self.table_qa_pairs.items():
            if keyword in question:
                selected.update(tables)
        if not selected:
            selected = {"tracks", "albums", "artists"}
        return ", ".join(selected)

    def _mock_sql_generate(self, question: str) -> str:
        """模拟 SQL 生成"""
        for keyword, sql in self.sql_qa_pairs.items():
            if keyword in question:
                return sql
        # 兜底：返回一个简单的计数 SQL
        return "SELECT COUNT(*) as count FROM tracks"

    def _mock_sql_rewrite(self, question: str) -> str:
        """模拟 SQL 改写"""
        # 从 prompt 中提取原始 SQL
        sql_match = re.search(r"【原始 SQL】\s*\n(.+?)\n【", question, re.DOTALL)
        if sql_match:
            original_sql = sql_match.group(1).strip()
            # 模拟修复：如果是常见错误，返回修正版
            for keyword, sql in self.sql_qa_pairs.items():
                if keyword in question:
                    return sql
            return original_sql  # 返回原样（模拟无法改进）
        return "SELECT 1"

    def _mock_summarize(self, question: str) -> str:
        """模拟结果汇总"""
        # 提取用户问题
        q_match = re.search(r"【用户问题】\s*\n(.+?)\n【", question, re.DOTALL)
        user_q = q_match.group(1).strip() if q_match else "查询"

        # 提取查询结果
        result_match = re.search(r"【查询结果】\s*\n(.+?)\n【", question, re.DOTALL)
        if not result_match:
            # 尝试匹配到结尾
            result_match = re.search(r"【查询结果】\s*\n(.+)$", question, re.DOTALL)

        result_text = result_match.group(1).strip() if result_match else "无结果"

        if "多少首歌曲" in user_q or "歌曲总数" in user_q:
            # 提取字段值（跳过 "共 N 条结果" 这类计数行）
            count = re.search(r"\w+\s*[:=]\s*(\d+)", result_text)
            if count:
                return f"数据库中共有 {count.group(1)} 首歌曲。"
        elif "销量最高的音乐类型" in user_q or "最受欢迎的音乐类型" in user_q:
            genre_match = re.search(r"genre_name:\s*(\w+)", result_text)
            count_match = re.search(r"sale_count:\s*(\d+)", result_text)
            if genre_match and count_match:
                return f"销量最高的音乐类型是 {genre_match.group(1)}，共售出 {count_match.group(1)} 首歌曲。"
        elif "哪个国家的客户最多" in user_q or "客户最多的国家" in user_q:
            country_match = re.search(r"Country:\s*(\w+)", result_text)
            count_match = re.search(r"customer_count:\s*(\d+)", result_text)
            if country_match and count_match:
                return f"客户数量最多的国家是 {country_match.group(1)}，共有 {count_match.group(1)} 位客户。"
        elif "总销售额" in user_q or "销售总额" in user_q:
            sales_match = re.search(r"total_sales:\s*([\d.]+)", result_text)
            if sales_match:
                return f"总销售额为 ${sales_match.group(1)} 美元。"
        elif "多少客户" in user_q or "客户总数" in user_q:
            count = re.search(r"\w+\s*[:=]\s*(\d+)", result_text)
            if count:
                return f"共有 {count.group(1)} 位客户。"

        # 兜底回答
        return f"根据查询结果：{result_text[:200]}"


# ============================================================
# Mock SQL Agent - 使用 Mock LLM 的 Agent
# ============================================================
class MockSQLAgent(SQLAgent):
    """使用 Mock LLM 的 SQL Agent，用于演示和测试"""

    def __init__(self, db_url: str):
        # 跳过父类初始化，手动初始化
        print("=" * 60)
        print("SQL Agent (Mock 模式) 初始化...")
        print("=" * 60)

        # 初始化 Mock LLM
        self.llm = MockLLM()

        # 初始化数据库解析器
        print("\n[步骤 0] 数据库解析中...")
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
        print("SQL Agent (Mock 模式) 初始化完成！")
        print("=" * 60)


# ============================================================
# 演示函数
# ============================================================
def demo_database_parser(db_url: str):
    """演示：数据库解析"""
    print("\n" + "=" * 60)
    print("【演示 1】数据库解析 (DBParser)")
    print("=" * 60)

    t0 = time.time()
    parser = DBParser(db_url)
    elapsed = time.time() - t0

    print(f"\n✓ 解析完成，耗时 {elapsed:.2f}s")
    print(f"  - 表数量: {len(parser.tables)}")
    print(f"  - 外键关系: {len(parser.foreign_keys)}")

    print(f"\n表列表:")
    for tname, tinfo in parser.tables.items():
        print(f"  • {tname} ({tinfo.row_count} 行, {len(tinfo.columns)} 字段)")

    print(f"\n表间关系 (外键):")
    for fk in parser.foreign_keys[:5]:
        print(f"  • {fk['constrained_table']}.{fk['constrained_columns'][0]} "
              f"→ {fk['referred_table']}.{fk['referred_columns'][0]}")
    if len(parser.foreign_keys) > 5:
        print(f"  ... 还有 {len(parser.foreign_keys) - 5} 个关系")

    # 展示一张表的详细信息
    print(f"\ntracks 表字段详情:")
    df = parser.get_table_fields_df("tracks")
    print(df.to_string(index=False))

    return parser


def demo_sql_validation(parser: DBParser):
    """演示：SQL 校验"""
    print("\n" + "=" * 60)
    print("【演示 2】SQL 校验 (SQLValidator)")
    print("=" * 60)

    validator = SQLValidator(parser)

    test_cases = [
        ("SELECT * FROM tracks", "正确的 SQL"),
        ("SELECT * FROM nonexistent", "不存在的表"),
        ("SELECT wrong_column FROM tracks", "不存在的字段"),
        ("SELECT COUNT(*) FROM tracks", "聚合查询"),
        ("DROP TABLE tracks", "非 SELECT 语句"),
    ]

    for sql, desc in test_cases:
        is_valid, err_msg, details = validator.validate(sql)
        status = "✓ 通过" if is_valid else "✗ 失败"
        print(f"\n  [{desc}]")
        print(f"    SQL: {sql}")
        print(f"    结果: {status}")
        if not is_valid:
            print(f"    错误: {err_msg[:80]}")


def demo_sql_execution(parser: DBParser):
    """演示：SQL 执行"""
    print("\n" + "=" * 60)
    print("【演示 3】SQL 执行")
    print("=" * 60)

    test_sqls = [
        "SELECT COUNT(*) as total FROM tracks",
        "SELECT Name, Composer FROM tracks LIMIT 5",
        "SELECT g.Name as genre, COUNT(*) as cnt FROM tracks t JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.GenreId ORDER BY cnt DESC LIMIT 5",
    ]

    for i, sql in enumerate(test_sqls, 1):
        print(f"\n  查询 {i}: {sql}")
        ok, result, cnt = parser.execute_sql(sql)
        if ok:
            print(f"    ✓ 成功，返回 {cnt} 行")
            print(f"    列: {', '.join(result['columns'])}")
            for row in result['rows'][:3]:
                print(f"    {row}")
            if cnt > 3:
                print(f"    ... 还有 {cnt - 3} 行")
            if result['info']:
                print(f"    注意: {result['info']}")
        else:
            print(f"    ✗ 失败: {str(result)[:80]}")


def demo_full_workflow(agent: SQLAgent, questions: List[str]):
    """演示：完整工作流"""
    print("\n" + "=" * 60)
    print("【演示 4】完整 SQL Agent 工作流")
    print("=" * 60)

    for i, question in enumerate(questions, 1):
        print(f"\n\n{'#'*60}")
        print(f"# 问题 {i}: {question}")
        print(f"{'#'*60}")

        result = agent.query(question)
        agent.print_steps(result)

        # 展示最终结果
        print(f"\n{'='*60}")
        print("📊 最终结果")
        print(f"{'='*60}")
        print(f"  问题: {result.question}")
        print(f"  SQL: {result.sql}")
        print(f"  选中表: {', '.join(result.selected_tables)}")
        print(f"  回答: {result.answer}")
        print(f"  成功: {'是' if result.success else '否'}")


# ============================================================
# 主入口
# ============================================================
def main():
    # 数据库路径
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinook.db")
    db_url = f"sqlite:///{db_path}"

    print("=" * 60)
    print("  SQL Agent 完整工作流演示")
    print("=" * 60)

    # 检查 API Key
    api_key = os.environ.get("GLM_API_KEY", "")
    use_mock = not api_key

    if use_mock:
        print("\n⚠️  未检测到 GLM_API_KEY 环境变量，将使用 Mock 模式演示")
        print("   设置 GLM_API_KEY 环境变量可使用真实大模型")
        print("   例如: $env:GLM_API_KEY=\"your_api_key\"")
    else:
        print(f"\n✓ 检测到 GLM_API_KEY，将使用真实大模型")

    input(f"\n按 Enter 开始演示...")

    # 演示 1: 数据库解析
    parser = demo_database_parser(db_url)
    input(f"\n按 Enter 继续...")

    # 演示 2: SQL 校验
    demo_sql_validation(parser)
    input(f"\n按 Enter 继续...")

    # 演示 3: SQL 执行
    demo_sql_execution(parser)
    input(f"\n按 Enter 继续...")

    # 演示 4: 完整工作流
    if use_mock:
        agent = MockSQLAgent(db_url)
    else:
        agent = SQLAgent(db_url=db_url, llm_api_key=api_key)

    questions = [
        "总共有多少首歌曲？",
        "销量最高的音乐类型是什么？",
        "哪个国家的客户最多？",
    ]

    demo_full_workflow(agent, questions)

    print("\n" + "=" * 60)
    print("  演示完成！")
    print("=" * 60)
    print("\n你可以通过以下方式使用 SQL Agent:")
    print("  from sql_agent import SQLAgent")
    print("  agent = SQLAgent(db_url='sqlite:///chinook.db', llm_api_key='your_key')")
    print("  result = agent.query('你的问题')")
    print("  print(result.answer)")


if __name__ == "__main__":
    main()
