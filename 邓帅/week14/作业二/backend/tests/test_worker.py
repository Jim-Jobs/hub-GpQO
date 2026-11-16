"""worker 状态机单测：mock mineru 与向量库，验证 pending->parsing->done/failed 与幂等语义。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mrag.worker.main as wmain
from mrag.models import STATUS_DONE, STATUS_FAILED, STATUS_PARSING, STATUS_PENDING, Base, FileRow


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    with S() as s:
        yield s


def _row(session, name="t.pdf"):
    r = FileRow(filename=name, file_path=f"/tmp/{name}", status=STATUS_PENDING)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def test_process_done(session, monkeypatch, tmp_path):
    row = _row(session)
    md = tmp_path / "t.md"
    md.write_text("# 标题\n\n" + ("内容文字。" * 60))

    def _set_done(sess, r):
        r.status = STATUS_DONE
        r.chunk_count = 3  # 真实 ingest_markdown 会写 chunk 数，mock 同步语义
        sess.commit()

    monkeypatch.setattr(wmain, "run_mineru", lambda *a, **k: md)
    monkeypatch.setattr(wmain, "ingest_markdown", lambda sess, r, m, client=None: (_set_done(sess, r), 3)[1])
    out = wmain.process_message(session, {"file_id": row.id, "file_path": row.file_path, "out_dir": "x"})
    assert out == "done"
    session.refresh(row)
    assert row.status == STATUS_DONE and row.chunk_count == 3


def test_process_marks_parsing_then_done(session, monkeypatch, tmp_path):
    """收消息后先置 parsing（条件更新防重复），成功后置 done。"""
    row = _row(session)
    md = tmp_path / "t.md"
    md.write_text("x" * 300)
    seen_statuses = []

    def fake_ingest(sess, r, m, client=None):
        sess.refresh(r)
        seen_statuses.append(r.status)
        r.status = STATUS_DONE
        r.chunk_count = 1
        sess.commit()
        return 1

    monkeypatch.setattr(wmain, "run_mineru", lambda *a, **k: md)
    monkeypatch.setattr(wmain, "ingest_markdown", fake_ingest)
    wmain.process_message(session, {"file_id": row.id, "file_path": row.file_path, "out_dir": "x"})
    assert seen_statuses == [STATUS_PARSING]  # ingest 执行时已是解析中


def test_process_mineru_failure_marks_failed(session, monkeypatch):
    row = _row(session)

    def boom(*a, **k):
        raise wmain.MineruError("mineru 退出码 1: bad pdf")

    monkeypatch.setattr(wmain, "run_mineru", boom)
    out = wmain.process_message(session, {"file_id": row.id, "file_path": row.file_path, "out_dir": "x"})
    assert out == "failed"
    session.refresh(row)
    assert row.status == STATUS_FAILED
    assert "bad pdf" in row.error


def test_process_unexpected_exception_marks_failed(session, monkeypatch, tmp_path):
    row = _row(session)

    def boom_ingest(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(wmain, "run_mineru", lambda *a, **k: tmp_path / "t.md")
    monkeypatch.setattr(wmain, "ingest_markdown", boom_ingest)
    out = wmain.process_message(session, {"file_id": row.id, "file_path": row.file_path, "out_dir": "x"})
    assert out == "failed"
    session.refresh(row)
    assert "kaboom" in row.error


def test_process_missing_row_skipped(session):
    out = wmain.process_message(session, {"file_id": 999, "file_path": "x", "out_dir": "y"})
    assert out == "skipped"


def test_status_flow_constants():
    assert (STATUS_PENDING, STATUS_PARSING, STATUS_DONE, STATUS_FAILED) == (
        "pending",
        "parsing",
        "done",
        "failed",
    )
