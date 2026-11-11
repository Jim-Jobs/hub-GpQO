from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.db import Session
from app.models import File
from app.worker import ParseWorker


def _upload(client, kb: str = "kb-worker") -> dict:
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": kb},
        files=[("files", ("doc.pdf", b"%PDF-1.4 worker", "application/pdf"))],
    )
    assert response.status_code == 200
    return response.json()["items"][0]


def test_worker_initializes_database(monkeypatch):
    import app.worker as worker_module

    called = []
    monkeypatch.setattr(worker_module, "init_db", lambda: called.append(True))

    worker_module.ParseWorker(get_settings())

    assert called


def test_worker_sets_and_resets_trace_context(monkeypatch):
    from app.logging_setup import trace_id_var

    worker = ParseWorker(get_settings())
    captured = {}

    def fake_process(payload):
        captured["trace_id"] = trace_id_var.get()
        return "OK"

    monkeypatch.setattr(worker, "_process_payload", fake_process)

    assert worker.process_payload({"trace_id": "trace-worker-1"}) == "OK"
    assert captured["trace_id"] == "trace-worker-1"
    assert trace_id_var.get() == "-"


def test_worker_processes_pending_file_to_done(client):
    item = _upload(client)
    file_id = item["file_id"]
    worker = ParseWorker(get_settings())

    payload = worker.task_payload(file_id)
    assert payload is not None
    assert worker.process_payload(payload) == "DONE"

    with Session() as session:
        row = session.get(File, file_id)
        assert row.status == "DONE"
        assert row.page_count == 3
        assert row.chunk_count >= 3
        assert row.finished_at is not None

    stem = Path(row.filepath).stem
    assert (
        Path(get_settings().processed_dir) / stem / "auto" / "content_list.json"
    ).is_file()

    assert worker.process_payload(payload) == "SKIP_DONE"


def test_worker_parse_failure_marks_failed_and_writes_dlq(client, monkeypatch):
    import app.worker as worker_module
    from app.ingest.parser import ParseError

    def boom(*_args, **_kwargs):
        raise ParseError("PARSE_FAILED", "boom")

    monkeypatch.setattr(worker_module, "parse_document", boom)

    item = _upload(client, kb="kb-worker-fail")
    worker = ParseWorker(get_settings())

    assert worker.process_payload(worker.task_payload(item["file_id"])) == "FAILED"

    with Session() as session:
        row = session.get(File, item["file_id"])
        assert row.status == "FAILED"
        assert row.error_code == "PARSE_FAILED"

    dlq = get_settings().worker_dlq_path
    assert dlq.is_file()
    assert "boom" in dlq.read_text(encoding="utf-8")


def test_process_pending_once_handles_all_pending(client):
    _upload(client, kb="kb-batch-1")
    _upload(client, kb="kb-batch-2")

    worker = ParseWorker(get_settings())
    assert worker.process_pending_once() == 2

    with Session() as session:
        statuses = [row.status for row in session.query(File).all()]
    assert statuses == ["DONE", "DONE"]
