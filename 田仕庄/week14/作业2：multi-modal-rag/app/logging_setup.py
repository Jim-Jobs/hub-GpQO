"""日志初始化：控制台 + 进程文件 + error.log，支持 trace_id 与 JSON 输出。"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from app.config import get_settings

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class TraceIdFilter(logging.Filter):
    """把当前请求的 trace_id 注入每条日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "pid": record.process,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _remove_rag_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, "_rag_handler", False):
            root.removeHandler(handler)
            handler.close()


def setup_logging(process: str) -> None:
    """API / worker / test 各调用一次；重复调用会替换旧 handler。"""
    settings = get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    _remove_rag_handlers(root)

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    formatter: logging.Formatter
    if settings.log_json:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            settings.log_format, datefmt="%Y-%m-%d %H:%M:%S"
        )
    trace_filter = TraceIdFilter()

    def build_handler(path, handler_level: int) -> RotatingFileHandler:
        handler = RotatingFileHandler(
            path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setLevel(handler_level)
        handler.setFormatter(formatter)
        handler.addFilter(trace_filter)
        handler._rag_handler = True  # type: ignore[attr-defined]
        return handler

    handlers: list[logging.Handler] = [
        build_handler(settings.logs_dir / f"{process}.log", level),
        build_handler(settings.logs_dir / "error.log", logging.ERROR),
    ]

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    console._rag_handler = True  # type: ignore[attr-defined]
    handlers.append(console)

    for handler in handlers:
        root.addHandler(handler)

    if process == "app":
        logging.getLogger("uvicorn.access").disabled = True

    logging.getLogger(__name__).debug(
        "logging configured: process=%s level=%s dir=%s",
        process,
        settings.log_level,
        settings.logs_dir,
    )


def shutdown_logging() -> None:
    """关闭本进程安装的日志 handler，释放文件句柄（Windows 删除日志目录前必须）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_rag_handler", False):
            root.removeHandler(handler)
            try:
                handler.flush()
            finally:
                handler.close()
