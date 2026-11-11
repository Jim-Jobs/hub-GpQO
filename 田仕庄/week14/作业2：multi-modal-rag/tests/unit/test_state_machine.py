from __future__ import annotations

import pytest

from app.db import Session
from app.ingest import state
from app.models import File


def _new_file(status: str = state.PENDING, kb: str = "kb") -> int:
    with Session() as session:
        row = File(
            filename="a.pdf",
            filepath="uploads/a.pdf",
            status=status,
            knowledge_base_id=kb,
        )
        session.add(row)
        session.flush()
        file_id = row.id
        session.commit()
    return file_id


def test_legal_transitions():
    file_id = _new_file()

    assert state.mark_parsing(file_id, attempt=1)
    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.PARSING
        assert row.attempts == 1
        assert row.started_at is not None

    assert state.mark_done(file_id, page_count=2, chunk_count=3)

    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.DONE
        assert row.page_count == 2
        assert row.chunk_count == 3
        assert row.finished_at is not None


def test_illegal_transition_raises():
    file_id = _new_file(state.DONE)

    with pytest.raises(state.TransitionError):
        state.transition(file_id, state.PARSING)


def test_expect_mismatch_returns_false():
    file_id = _new_file(state.PENDING)

    assert state.transition(file_id, state.PARSING, expect=state.PARSING) is False


def test_mark_done_requires_parsing():
    file_id = _new_file(state.PENDING)

    assert state.mark_done(file_id) is False


def test_failed_then_reparse_resets_error():
    file_id = _new_file(state.PENDING)

    assert state.mark_failed(file_id, code="PARSE_FAILED", message="boom")
    assert state.reset_for_reparse(file_id)

    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.PENDING
        assert row.error_code is None
        assert row.error_message is None
        assert row.finished_at is None


def test_status_text():
    assert state.status_text(state.PENDING) == "待解析"
    assert state.status_text(state.FAILED) == "解析失败"
    assert state.status_text("UNKNOWN") == "UNKNOWN"
