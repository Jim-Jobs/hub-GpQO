"""history 持久化测试:落库、倒序列表、JSON 字段往返(离线,用临时库)。"""
import pytest

from app import history


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """每个测试用独立临时 history 库。"""
    fake = tmp_path / "history.db"
    monkeypatch.setattr(history, "HISTORY_DB_PATH", fake)
    history.init_db()
    return fake


def _sample(question: str, status: str = "success") -> dict:
    return {
        "question": question,
        "status": status,
        "tables": ["customers", "invoices"],
        "sql": "SELECT 1",
        "summary": "测试汇总",
        "columns": ["a"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "rewrite_rounds": 0,
        "duration_ms": 123,
        "steps": [{"step": 1, "name": "数据库解析", "status": "done"}],
    }


def test_save_and_list_roundtrip(tmp_db):
    history.save_record(_sample("问题一"))
    records = history.list_records()
    assert len(records) == 1
    r = records[0]
    assert r["question"] == "问题一"
    assert r["status"] == "success"
    assert r["tables"] == ["customers", "invoices"]        # JSON list 往返
    assert r["steps"][0]["name"] == "数据库解析"            # JSON dict 往返
    assert r["rows"] == [[1]]
    assert r["id"] >= 1
    assert r["created_at"]                                  # 时间戳存在


def test_list_orders_newest_first(tmp_db):
    history.save_record(_sample("第一问"))
    history.save_record(_sample("第二问"))
    records = history.list_records()
    assert [r["question"] for r in records] == ["第二问", "第一问"]


def test_failed_record_kept_with_error(tmp_db):
    """失败问答落库,真实失败原因(error)完整往返,供回放展示。"""
    rec = _sample("失败问", status="failed")
    rec["summary"] = ""
    rec["sql"] = "SELECT bad"
    rec["error"] = "SQL 校验错误未能在改写重试内修复: no such column: bad"
    history.save_record(rec)
    r = history.list_records()[0]
    assert r["status"] == "failed"
    assert r["summary"] == ""
    assert "no such column: bad" in r["error"]


def test_list_limit(tmp_db):
    for i in range(7):
        history.save_record(_sample(f"问题{i}"))
    assert len(history.list_records(limit=5)) == 5
