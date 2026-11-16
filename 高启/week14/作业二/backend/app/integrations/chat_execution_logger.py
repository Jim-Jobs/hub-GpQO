"""以 JSON Lines 和大小轮转记录问答阶段元数据。"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

from backend.app.chat.domain.models import ChatExecutionEvent


class _JsonLineFormatter(logging.Formatter):
    """把结构化字典编码为每行一个 JSON 对象。"""

    def format(self, record: logging.LogRecord) -> str:
        """序列化 logger.info 接收的安全事件字段。"""
        return json.dumps(record.msg, ensure_ascii=False, separators=(",", ":"))


class ChatExecutionLogger:
    """将问答阶段事件写入单个轮转 JSONL 文件。"""

    def __init__(self, logs_dir: Path, max_bytes: int, backup_count: int) -> None:
        """创建日志目录和 UTF-8 轮转 handler。"""
        if max_bytes < 1 or backup_count < 1:
            raise ValueError("日志大小和轮转文件数必须为正整数")
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._logger = logging.getLogger(f"rag.chat.execution.{logs_dir.resolve()}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        log_path = logs_dir / "chat.jsonl"
        has_file_handler = any(
            getattr(handler, "baseFilename", None) == str(log_path.resolve())
            for handler in self._logger.handlers
        )
        if not has_file_handler:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            handler.setFormatter(_JsonLineFormatter())
            self._logger.addHandler(handler)

    def write(self, event: ChatExecutionEvent) -> None:
        """写入不含问题、图片、提示词或检索正文的阶段元数据。"""
        fields = {
            "chat_id": event.chat_id,
            "timestamp": event.timestamp.isoformat(),
            "stage": event.stage.value,
            "event_type": event.event_type,
            "duration_ms": event.duration_ms,
            "model_name": event.model_name,
            "retrieved_count": event.retrieved_count,
            "error_code": event.error_code,
        }
        with self._lock:
            self._logger.info(fields)

    def close(self) -> None:
        """关闭当前 logger 的文件 handler，供应用生命周期释放资源。"""
        with self._lock:
            for handler in list(self._logger.handlers):
                handler.flush()
                handler.close()
                self._logger.removeHandler(handler)
