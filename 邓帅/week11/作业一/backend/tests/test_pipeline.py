"""pipeline 测试:选表输出解析、执行截断、workflow 编排(LLM 全 mock,离线)。"""
import asyncio
import sqlite3
from collections import deque

from app.config import DB_PATH, DB_URL
from app.pipeline import runner, steps

# ---------- module 级共享资源(DBParser 初始化有统计开销,只跑一次) ----------

_parser = None
_ro_conn = None


def get_parser():
    global _parser
    if _parser is None:
        from app.db_parser import DBParser
        _parser = DBParser(DB_URL)
    return _parser


def get_ro_conn() -> sqlite3.Connection:
    global _ro_conn
    if _ro_conn is None:
        _ro_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    return _ro_conn


def run_flow(question: str) -> list[dict]:
    """同步驱动 workflow,收集全部事件。"""

    async def _collect():
        return [
            ev
            async for ev in runner.run_workflow(
                question, parser=get_parser(), conn=get_ro_conn()
            )
        ]

    return asyncio.run(_collect())


def mock_chat(monkeypatch, replies: list[str]):
    """按调用顺序弹出固定回复,记录收到的 prompt。"""
    queue = deque(replies)
    calls: list[str] = []

    async def fake_chat(prompt, system=None, temperature=0.2):
        calls.append(prompt)
        return queue.popleft()

    monkeypatch.setattr(steps, "chat", fake_chat)
    return calls


# ---------- parse_tables_output:选表结果解析 ----------

def test_parse_tables_plain_json():
    out = steps.parse_tables_output('["customers", "invoices"]', ["customers", "invoices", "tracks"])
    assert out == ["customers", "invoices"]


def test_parse_tables_fenced_json():
    out = steps.parse_tables_output('```json\n["customers"]\n```', ["customers", "invoices"])
    assert out == ["customers"]


def test_parse_tables_json_embedded_in_text():
    out = steps.parse_tables_output("根据分析需要 [\"tracks\"] 这张表", ["tracks", "albums"])
    assert out == ["tracks"]


def test_parse_tables_dict_form():
    out = steps.parse_tables_output('{"tables": ["tracks"]}', ["tracks", "albums"])
    assert out == ["tracks"]


def test_parse_tables_filters_invalid():
    out = steps.parse_tables_output('["customers", "hack"]', ["customers", "invoices"])
    assert out == ["customers"]


def test_parse_tables_plain_text_mentions():
    out = steps.parse_tables_output("需要用到 customers 和 invoices 两张表", ["customers", "invoices"])
    assert out == ["customers", "invoices"]


def test_parse_tables_empty_when_none_valid():
    assert steps.parse_tables_output('["nope"]', ["customers"]) == []


# ---------- execute_query:只读执行与截断 ----------

def test_execute_query_truncates():
    r = steps.execute_query("SELECT * FROM tracks", get_ro_conn(), row_limit=10)
    assert r["columns"] == ["TrackId", "Name", "AlbumId", "MediaTypeId", "GenreId", "Composer", "Milliseconds", "Bytes", "UnitPrice"]
    assert r["row_count"] == 10
    assert r["truncated"] is True


def test_execute_query_small_table_not_truncated():
    r = steps.execute_query("SELECT * FROM genres", get_ro_conn(), row_limit=50)
    assert r["row_count"] == 25
    assert r["truncated"] is False


# ---------- run_workflow:全链路编排 ----------

def test_flow_success_without_rewrite(monkeypatch):
    """直通:选表→生成→校验过→执行→汇总,改写未触发(rounds=0)。"""
    mock_chat(monkeypatch, [
        '["customers", "invoices"]',
        "SELECT c.Country, SUM(i.Total) AS total FROM invoices i "
        "JOIN customers c ON i.CustomerId = c.CustomerId "
        "GROUP BY c.Country ORDER BY total DESC",
        "美国客户的消费总额最高。",
    ])
    events = run_flow("哪个国家的客户消费总额最高?")

    final = events[-1]
    assert final["event"] == "final" and final["status"] == "success"
    assert "JOIN" in final["sql"]
    assert final["summary"] == "美国客户的消费总额最高。"
    assert final["tables"] == ["customers", "invoices"]
    assert final["rewrite_rounds"] == 0
    assert final["row_count"] > 0

    # 7 步全部有 done 事件
    done_steps = [e["step"] for e in events if e.get("status") == "done"]
    assert done_steps == [1, 2, 3, 4, 5, 6, 7]


def test_flow_rewrite_recovers(monkeypatch):
    """坏 SQL → 校验失败 → 改写一次成功。"""
    mock_chat(monkeypatch, [
        '["albums"]',
        "SELECT bad_col FROM albums",
        "SELECT Title FROM albums LIMIT 3",
        "查到 3 张专辑。",
    ])
    events = run_flow("查询几张专辑的名称")
    final = events[-1]

    assert final["status"] == "success"
    assert final["rewrite_rounds"] == 1
    assert final["sql"] == "SELECT Title FROM albums LIMIT 3"
    assert final["row_count"] == 3

    # 改写步 payload 带走了错误信息
    rewrite_done = next(e for e in events if e["step"] == 5 and e.get("status") == "done")
    assert rewrite_done["data"]["rounds"] == 1
    assert any("bad_col" in str(err) for err in rewrite_done["data"]["errors"])


def test_flow_fails_after_max_rounds(monkeypatch):
    """生成 + 3 次改写全失败 → final failed,文案标明校验源头。"""
    mock_chat(monkeypatch, [
        '["albums"]',
        "SELECT bad_col FROM albums",
        "SELECT bad_col FROM albums",
        "SELECT bad_col FROM albums",
        "SELECT bad_col FROM albums",
    ])
    events = run_flow("查询专辑")
    final = events[-1]

    assert final["status"] == "failed"
    assert final["rewrite_rounds"] == 3
    assert "校验错误" in final["error"]
    assert "bad_col" in final["error"]


def test_flow_execute_failure_message(monkeypatch):
    """校验通过但执行报错 → 改写耗尽后文案标明执行源头(非校验)。"""
    mock_chat(monkeypatch, [
        '["albums"]',
        "SELECT Title FROM albums",
        "SELECT Title FROM albums",
        "SELECT Title FROM albums",
        "SELECT Title FROM albums",
    ])

    def fake_execute(sql, conn, row_limit):
        return {"error": "SQL 执行失败: disk I/O error"}

    monkeypatch.setattr(steps, "execute_query", fake_execute)
    events = run_flow("查询专辑名")
    final = events[-1]

    assert final["status"] == "failed"
    assert "执行错误" in final["error"]
    assert "disk I/O error" in final["error"]


def test_flow_dangerous_sql_redirected(monkeypatch):
    """危险 SQL 被白名单拦截后进入改写,改写后成功。"""
    mock_chat(monkeypatch, [
        '["albums"]',
        "DELETE FROM albums",
        "SELECT * FROM albums LIMIT 1",
        "查询正常。",
    ])
    events = run_flow("删掉专辑表")
    final = events[-1]

    assert final["status"] == "success"
    assert final["rewrite_rounds"] == 1
    assert final["sql"].startswith("SELECT")


def test_flow_empty_table_selection_fallback(monkeypatch):
    """选表输出全非法 → 兜底用全部业务表。"""
    calls = mock_chat(monkeypatch, [
        '["nope"]',
        "SELECT COUNT(*) AS n FROM albums",
        "共 347 张专辑。",
    ])
    events = run_flow("数据库里有多少张专辑")
    final = events[-1]

    assert final["status"] == "success"
    # 兜底:选表步 data 标记 fallback,final.tables 为全部业务表
    select_done = next(e for e in events if e["step"] == 2 and e.get("status") == "done")
    assert select_done["data"]["fallback"] is True
    assert "albums" in final["tables"]
    # 生成 prompt 里应包含全部业务表清单(兜底生效)
    assert "employees" in calls[1]
