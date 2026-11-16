"""管理进程内 FIFO 问答队列、状态流转和后台执行线程。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable
from uuid import uuid4

from backend.app.chat.application.ports import ChatStatusStore
from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import (
    ChatExecutionEvent,
    ChatRequest,
    ChatResponse,
    ChatStatusRecord,
)
from backend.app.core.config import Settings
from backend.app.integrations.chat_execution_logger import ChatExecutionLogger


logger = logging.getLogger(__name__)
StageCallback = Callable[[ChatStage, int], None]
ChatProcessor = Callable[[ChatRequest, StageCallback], ChatResponse]


class ChatQueueFullError(RuntimeError):
    """等待队列已满，调用方应返回 HTTP 429 且不保存新状态。"""


class ChatNotFoundError(LookupError):
    """请求的 chat_id 不存在或已过期。"""


class ChatConflictError(RuntimeError):
    """任务当前状态不允许执行所请求的确认操作。"""


class ChatRequestError(ValueError):
    """提交的问答任务缺少必要内容。"""


@dataclass(frozen=True)
class _QueuedChat:
    """仅存在于进程内队列的用户内容和确认回答模式。"""

    chat_id: str
    request: ChatRequest
    general_only: bool = False


class ChatTaskManager:
    """持有有界 FIFO 队列；Redis 只保存状态和结果，不保存用户输入。"""

    def __init__(
        self,
        settings: Settings,
        status_store: ChatStatusStore,
        process_chat: ChatProcessor,
        process_general: ChatProcessor,
        *,
        execution_logger: ChatExecutionLogger | None = None,
    ) -> None:
        """保存任务依赖，并按配置创建有界队列和 worker 停止信号。"""
        self._settings = settings
        self._status_store = status_store
        self._process_chat = process_chat
        self._process_general = process_general
        self._execution_logger = execution_logger or ChatExecutionLogger(
            settings.logs_dir,
            settings.chat_log_max_bytes,
            settings.chat_log_backup_count,
        )
        self._queue: Queue[_QueuedChat] = Queue(maxsize=settings.chat_queue_capacity)
        self._submit_lock = Lock()
        self._stop_event = Event()
        self._threads: list[Thread] = []

    def start(self) -> int:
        """恢复中断状态并启动配置数量的后台 worker。"""
        if self._threads:
            raise RuntimeError("ChatTaskManager 不能重复启动")
        failed_count = self._status_store.fail_interrupted()
        for index in range(self._settings.chat_max_concurrency):
            thread = Thread(
                target=self._worker,
                name=f"chat-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        return failed_count

    def stop(self, timeout: float = 5) -> None:
        """通知 worker 停止并等待当前同步模型调用结束。"""
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        if all(not thread.is_alive() for thread in self._threads):
            self._execution_logger.close()

    def submit(self, request: ChatRequest) -> ChatStatusRecord:
        """在队列有空间时先保存 accepted 状态，再原子入队请求。"""
        if not request.question_text.strip():
            raise ChatRequestError("问题文本不能为空")
        with self._submit_lock:
            self._ensure_started()
            if self._queue.full():
                raise ChatQueueFullError("问答队列已满，请稍后重试")
            record = ChatStatusRecord(
                str(uuid4()), ChatStatus.ACCEPTED, ChatStage.SCOPE_AND_REWRITE
            )
            self._status_store.save(record)
            self._write_event(
                record.chat_id,
                record.stage,
                "accepted",
            )
            self._queue.put_nowait(_QueuedChat(record.chat_id, request))
        return record

    def get_status(self, chat_id: str) -> ChatStatusRecord:
        """读取任务状态；未知或过期的 chat_id 抛出未找到错误。"""
        record = self._status_store.get(chat_id)
        if record is None:
            raise ChatNotFoundError("问答任务不存在或已过期")
        return record

    def confirm(
        self,
        chat_id: str,
        *,
        accept_general_answer: bool,
        request: ChatRequest | None = None,
    ) -> ChatStatusRecord:
        """取消等待确认任务，或将重传内容排入不检索的通用回答路径。"""
        with self._submit_lock:
            self._ensure_started()
            record = self._status_store.get(chat_id)
            if record is None:
                raise ChatNotFoundError("问答任务不存在或已过期")
            if record.status is not ChatStatus.NEEDS_CONFIRMATION:
                raise ChatConflictError("问答任务当前不在等待确认状态")
            if not accept_general_answer:
                cancelled = replace(
                    record,
                    status=ChatStatus.CANCELLED,
                    stage=ChatStage.AWAITING_CONFIRMATION,
                    updated_at=datetime.now(timezone.utc),
                )
                self._status_store.save(cancelled)
                self._write_event(
                    chat_id,
                    ChatStage.AWAITING_CONFIRMATION,
                    "cancelled",
                )
                return cancelled
            if request is None or not request.question_text.strip():
                raise ChatRequestError("接受通用回答时必须重传原问题")
            if self._queue.full():
                raise ChatQueueFullError("问答队列已满，请稍后重试")
            accepted = replace(
                record,
                status=ChatStatus.ACCEPTED,
                stage=ChatStage.ANSWER_GENERATION,
                response=None,
                error=None,
                updated_at=datetime.now(timezone.utc),
            )
            self._status_store.save(accepted)
            self._write_event(
                chat_id,
                ChatStage.ANSWER_GENERATION,
                "accepted",
            )
            self._queue.put_nowait(_QueuedChat(chat_id, request, general_only=True))
            return accepted

    def _ensure_started(self) -> None:
        """拒绝在应用生命周期启动 worker 前接收任务。"""
        if not self._threads or self._stop_event.is_set():
            raise RuntimeError("ChatTaskManager 尚未启动或已经停止")

    def _worker(self) -> None:
        """持续取出 FIFO 任务并隔离单个任务异常，避免 worker 退出。"""
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                if not self._stop_event.is_set():
                    try:
                        self._execute(task)
                    except Exception as error:
                        # 单任务状态仓库故障不能让同一进程中的 worker 永久退出。
                        logger.error(
                            "问答任务协调异常 chat_id=%s error_type=%s",
                            task.chat_id,
                            type(error).__name__,
                        )
            finally:
                self._queue.task_done()

    def _execute(self, task: _QueuedChat) -> None:
        """运行一个 ChatAgent 或通用回答，并保存完成、确认或失败状态。"""
        try:
            record = self._status_store.get(task.chat_id)
        except Exception as error:
            logger.error(
                "问答任务初始状态读取失败 chat_id=%s error_type=%s",
                task.chat_id,
                type(error).__name__,
            )
            self._save_retried_failure(task.chat_id)
            return
        if record is None:
            return
        current_stage: ChatStage | None = None
        stage_started_at = 0.0
        retrieved_count = 0

        def enter_stage(stage: ChatStage, count: int = 0) -> None:
            """结束上一阶段并持久化新阶段开始事件和状态。"""
            nonlocal current_stage, stage_started_at, retrieved_count, record
            if current_stage is stage:
                retrieved_count = count
                return
            if current_stage is not None:
                self._log_stage(
                    task.chat_id,
                    current_stage,
                    "completed",
                    stage_started_at,
                    retrieved_count,
                )
            record = replace(
                record,
                status=ChatStatus.RUNNING,
                stage=stage,
                response=None,
                error=None,
                updated_at=datetime.now(timezone.utc),
            )
            self._status_store.save(record)
            current_stage = stage
            stage_started_at = monotonic()
            retrieved_count = count
            self._write_event(task.chat_id, stage, "started", retrieved_count=count)

        try:
            enter_stage(
                ChatStage.ANSWER_GENERATION
                if task.general_only
                else ChatStage.SCOPE_AND_REWRITE
            )
            if task.general_only:
                response = self._process_general(task.request, enter_stage)
            else:
                response = self._process_chat(task.request, enter_stage)
            if current_stage is not None:
                self._log_stage(
                    task.chat_id,
                    current_stage,
                    "completed",
                    stage_started_at,
                    retrieved_count,
                )
            waiting = response.route == "needs_confirmation"
            final_record = replace(
                record,
                status=(
                    ChatStatus.NEEDS_CONFIRMATION if waiting else ChatStatus.COMPLETED
                ),
                stage=(
                    ChatStage.AWAITING_CONFIRMATION
                    if waiting
                    else record.stage
                ),
                response=response,
                error=None,
                updated_at=datetime.now(timezone.utc),
            )
            self._status_store.save(final_record)
        except Exception as error:
            if current_stage is not None:
                self._write_event(
                    task.chat_id,
                    current_stage,
                    "failed",
                    duration_ms=_elapsed_ms(stage_started_at),
                    retrieved_count=retrieved_count,
                    error_code=type(error).__name__,
                )
            logger.error(
                "问答任务执行失败 chat_id=%s error_type=%s",
                task.chat_id,
                type(error).__name__,
            )
            failed = replace(
                record,
                status=ChatStatus.FAILED,
                error="问答处理失败，请重新提交。",
                updated_at=datetime.now(timezone.utc),
            )
            try:
                self._status_store.save(failed)
            except Exception:
                logger.error(
                    "问答失败状态写入 Redis 失败 chat_id=%s",
                    task.chat_id,
                )

    def _save_retried_failure(self, chat_id: str) -> None:
        """短暂读取故障后重读一次状态，并尽力保存安全的失败摘要。"""
        try:
            record = self._status_store.get(chat_id)
            if record is not None:
                self._status_store.save(
                    replace(
                        record,
                        status=ChatStatus.FAILED,
                        error="问答状态读取失败，请重新提交。",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as error:
            logger.error(
                "问答状态读取故障后无法保存失败状态 chat_id=%s error_type=%s",
                chat_id,
                type(error).__name__,
            )

    def _log_stage(
        self,
        chat_id: str,
        stage: ChatStage,
        event: str,
        started_at: float,
        retrieved_count: int,
    ) -> None:
        """记录阶段结束或失败所需的耗时、模型和检索数量。"""
        self._write_event(
            chat_id,
            stage,
            event,
            duration_ms=_elapsed_ms(started_at),
            retrieved_count=retrieved_count,
        )

    def _write_event(
        self,
        chat_id: str,
        stage: ChatStage,
        event_type: str,
        *,
        duration_ms: int | None = None,
        retrieved_count: int = 0,
        error_code: str | None = None,
    ) -> None:
        """构建类型化运行事件并交给 JSONL 适配器写入。"""
        self._execution_logger.write(
            ChatExecutionEvent(
                chat_id=chat_id,
                stage=stage,
                event_type=event_type,
                model_name=self._settings.chat_model,
                duration_ms=duration_ms,
                retrieved_count=retrieved_count,
                error_code=error_code,
            )
        )


def _elapsed_ms(started_at: float) -> int:
    """计算单个阶段的毫秒耗时，供 Redis 外的运行日志使用。"""
    return max(0, int((monotonic() - started_at) * 1000))
