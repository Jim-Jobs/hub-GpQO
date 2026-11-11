from __future__ import annotations

from datetime import timedelta

from app.db import Session, utcnow
from app.ingest import state
from app.ingest.queue import mock_sent
from app.ingest.sweeper import sweep_once
from app.models import File


def _make_file(
    status: str,
    *,
    created_at=None,
    started_at=None,
    kafka_sent: bool = True,
    requeue_count: int = 0,
) -> int:
    with Session() as session:
        row = File(
            filename="a.pdf",
            filepath="uploads/a.pdf",
            status=status,
            knowledge_base_id="kb-sweep",
            created_at=created_at or utcnow(),
            started_at=started_at,
            kafka_sent=kafka_sent,
            requeue_count=requeue_count,
        )
        session.add(row)
        session.flush()
        file_id = row.id
        session.commit()
    return file_id


def test_stale_pending_is_requeued():
    file_id = _make_file(
        state.PENDING,
        created_at=utcnow() - timedelta(seconds=9999),
    )

    stats = sweep_once()

    assert stats["requeued"] == 1
    assert len(mock_sent()) == 1

    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.PENDING
        assert row.requeue_count == 1
        assert row.kafka_sent is True


def test_stale_pending_beyond_max_requeue_fails():
    file_id = _make_file(
        state.PENDING,
        created_at=utcnow() - timedelta(seconds=9999),
        requeue_count=2,
    )

    stats = sweep_once()

    assert stats["failed_pending"] == 1
    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.FAILED
        assert row.error_code == "STALE_PENDING"
    assert mock_sent() == []


def test_stale_parsing_is_failed():
    file_id = _make_file(
        state.PARSING,
        started_at=utcnow() - timedelta(seconds=9999),
    )

    stats = sweep_once()

    assert stats["failed_parsing"] == 1
    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == state.FAILED
        assert row.error_code == "PARSE_INTERRUPTED"


def test_fresh_pending_is_untouched():
    file_id = _make_file(state.PENDING, created_at=utcnow())

    stats = sweep_once()

    assert stats == {
        "requeued": 0,
        "failed_pending": 0,
        "failed_parsing": 0,
        "cleaned_images": 0,
    }
    with Session() as session:
        assert session.get(File, file_id).status == state.PENDING
