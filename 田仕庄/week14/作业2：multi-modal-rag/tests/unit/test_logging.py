from __future__ import annotations

import logging


def test_logging_writes_process_file_with_trace(isolated_data_dir):
    from app.config import get_settings
    from app.logging_setup import setup_logging, trace_id_var

    settings = get_settings()
    setup_logging("test")

    logger = logging.getLogger("rag.test")
    token = trace_id_var.set("trace-abc123")
    try:
        logger.info("hello-logger")
        logger.error("boom-error")
    finally:
        trace_id_var.reset(token)

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = (settings.logs_dir / "test.log").read_text(encoding="utf-8")
    assert "hello-logger" in log_text
    assert "[trace-abc123]" in log_text

    error_text = (settings.logs_dir / "error.log").read_text(encoding="utf-8")
    assert "boom-error" in error_text


def test_setup_logging_is_idempotent(isolated_data_dir):
    import logging

    from app.logging_setup import setup_logging

    setup_logging("test")
    setup_logging("test")

    rag_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_rag_handler", False)
    ]
    assert len(rag_handlers) == 3
