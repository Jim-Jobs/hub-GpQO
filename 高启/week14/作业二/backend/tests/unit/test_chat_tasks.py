"""覆盖问答任务队列、Redis 状态、确认分支和结构化日志边界。"""

import json
from threading import Event, Lock
from time import monotonic, sleep

import pytest

from backend.app.chat.application.task_manager import (
    ChatConflictError,
    ChatNotFoundError,
    ChatQueueFullError,
    ChatTaskManager,
)
from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import (
    ChatExecutionEvent,
    ChatRequest,
    ChatResponse,
    ChatStatusRecord,
)
from backend.app.core.config import Settings
from backend.app.integrations.redis_chat_status_store import RedisChatStatusStore
from backend.app.integrations.chat_execution_logger import ChatExecutionLogger
from backend.app.integrations.errors import ChatStatusStoreError


class MemoryChatStatusStore:
    """在线程安全的内存字典中模拟 Redis 问答状态仓库。"""

    def __init__(self) -> None:
        """初始化记录表和同步锁。"""
        self.records: dict[str, ChatStatusRecord] = {}
        self._lock = Lock()

    def save(self, record: ChatStatusRecord) -> None:
        """按 chat_id 覆盖状态记录。"""
        with self._lock:
            self.records[record.chat_id] = record

    def get(self, chat_id: str) -> ChatStatusRecord | None:
        """读取状态；未知 chat_id 返回 None。"""
        with self._lock:
            return self.records.get(chat_id)

    def fail_interrupted(self) -> int:
        """把上次进程遗留的 accepted/running 状态改为 failed。"""
        with self._lock:
            count = 0
            for chat_id, record in list(self.records.items()):
                if record.status in {ChatStatus.ACCEPTED, ChatStatus.RUNNING}:
                    self.records[chat_id] = _failed(record)
                    count += 1
            return count


def _settings(tmp_path, **overrides) -> Settings:
    """创建使用临时数据根目录且允许指定队列参数的配置。"""
    values = {
        "RAG_DATA_ROOT": str(tmp_path),
        "RAG_CHAT_QUEUE_CAPACITY": "2",
        "RAG_CHAT_MAX_CONCURRENCY": "1",
        **overrides,
    }
    return Settings.from_env(values)


def _wait_for(predicate, timeout: float = 2) -> None:
    """在限定时间内等待异步 worker 达到指定的可观察状态。"""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("问答 worker 未在超时前达到预期状态")


def _failed(record: ChatStatusRecord) -> ChatStatusRecord:
    """生成安全的进程中断失败状态，不携带原问题或异常正文。"""
    from dataclasses import replace
    from datetime import datetime, timezone

    return replace(
        record,
        status=ChatStatus.FAILED,
        error="问答服务重启导致任务中断，请重新提交。",
        updated_at=datetime.now(timezone.utc),
    )


def test_redis_chat_status_store_sets_ttl_and_recovers_active_records() -> None:
    """状态写入使用 7 天 TTL，启动恢复只失败 accepted/running 任务。"""

    class FakeRedis:
        """保存 Redis 字符串与过期选项的轻量替身。"""

        def __init__(self) -> None:
            """初始化空 key/value 表和写入过期参数记录。"""
            self.values: dict[str, str] = {}
            self.expirations: list[int] = []

        def set(self, key: str, value: str, *, ex: int) -> None:
            """保存 JSON 值并记录相对过期秒数。"""
            self.values[key] = value
            self.expirations.append(ex)

        def get(self, key: str) -> bytes | None:
            """模拟 Redis 默认返回 UTF-8 字节串。"""
            value = self.values.get(key)
            return value.encode("utf-8") if value is not None else None

        def scan_iter(self, *, match: str):
            """返回符合简单前缀 pattern 的所有 key。"""
            prefix = match.removesuffix("*")
            return [key for key in self.values if key.startswith(prefix)]

    redis = FakeRedis()
    store = RedisChatStatusStore(redis, ttl_seconds=604800)
    accepted = ChatStatusRecord("accepted-id", ChatStatus.ACCEPTED, ChatStage.SCOPE_AND_REWRITE)
    running = ChatStatusRecord("running-id", ChatStatus.RUNNING, ChatStage.RETRIEVAL)
    waiting = ChatStatusRecord(
        "waiting-id", ChatStatus.NEEDS_CONFIRMATION, ChatStage.AWAITING_CONFIRMATION
    )
    store.save(accepted)
    store.save(running)
    store.save(waiting)

    assert store.get("accepted-id") == accepted
    assert "question_text" not in redis.values["rag:chat:accepted-id"]
    assert '"image"' not in redis.values["rag:chat:accepted-id"]
    assert redis.expirations == [604800, 604800, 604800]
    assert store.fail_interrupted() == 2
    assert store.get("accepted-id").status is ChatStatus.FAILED
    assert store.get("running-id").status is ChatStatus.FAILED
    assert store.get("waiting-id").status is ChatStatus.NEEDS_CONFIRMATION


def test_redis_chat_status_store_converts_invalid_payload_to_domain_error() -> None:
    """无效 UTF-8/JSON 状态统一映射为 ChatStatusStoreError。"""

    class InvalidRedis:
        """返回损坏 JSON 的 Redis 读取替身。"""

        def get(self, key: str) -> bytes:
            """模拟 Redis 中已有损坏的状态 JSON。"""
            return b"{invalid"

    with pytest.raises(ChatStatusStoreError, match="格式无效"):
        RedisChatStatusStore(InvalidRedis()).get("broken")


def test_task_manager_runs_fifo_and_persists_terminal_response(tmp_path) -> None:
    """单 worker 按入队顺序处理任务并持久化 completed 响应。"""
    store = MemoryChatStatusStore()
    release = Event()
    first_started = Event()
    processed: list[str] = []

    def process(request: ChatRequest, stage_callback) -> ChatResponse:
        """模拟 Agent 阶段回调并等待释放，以观测入队先后。"""
        stage_callback(ChatStage.SCOPE_AND_REWRITE, 0)
        first_started.set()
        assert release.wait(2)
        processed.append(request.question_text)
        return ChatResponse("in_scope", [{"type": "text", "text": request.question_text}])

    manager = ChatTaskManager(_settings(tmp_path), store, process, process)
    manager.start()
    first = manager.submit(ChatRequest("问题一"))
    assert first_started.wait(1)
    second = manager.submit(ChatRequest("问题二"))
    assert first.status is ChatStatus.ACCEPTED
    assert second.status is ChatStatus.ACCEPTED
    release.set()
    _wait_for(lambda: store.get(second.chat_id).status is ChatStatus.COMPLETED)
    manager.stop()

    assert processed == ["问题一", "问题二"]
    assert store.get(first.chat_id).response.answer_segments[0]["text"] == "问题一"
    assert store.get(second.chat_id).response.answer_segments[0]["text"] == "问题二"


def test_full_queue_rejects_without_creating_orphan_status(tmp_path) -> None:
    """运行中任务之外的等待队列满时返回错误且不留第三条状态记录。"""
    store = MemoryChatStatusStore()
    entered = Event()
    release = Event()

    def blocked_process(request: ChatRequest, stage_callback) -> ChatResponse:
        """保持首个任务运行，使后续任务可确定地填满等待队列。"""
        entered.set()
        assert release.wait(2)
        return ChatResponse("general", [{"type": "text", "text": "完成"}])

    manager = ChatTaskManager(_settings(tmp_path, RAG_CHAT_QUEUE_CAPACITY="1"), store,
                              blocked_process, blocked_process)
    manager.start()
    manager.submit(ChatRequest("运行中"))
    assert entered.wait(1)
    manager.submit(ChatRequest("排队中"))

    with pytest.raises(ChatQueueFullError):
        manager.submit(ChatRequest("应拒绝"))

    assert len(store.records) == 2
    release.set()
    manager.stop()


def test_confirmation_requeues_general_answer_without_retrieval(tmp_path) -> None:
    """接受确认将重传内容排入通用回答路径，不再次调用资料检索处理器。"""
    store = MemoryChatStatusStore()
    general_done = Event()
    calls = {"retrieval": 0, "general": 0}

    def insufficient(request: ChatRequest, stage_callback) -> ChatResponse:
        """模拟检索后证据不足并返回确认状态。"""
        calls["retrieval"] += 1
        stage_callback(ChatStage.AWAITING_CONFIRMATION, 0)
        return ChatResponse(
            "needs_confirmation", [{"type": "text", "text": "需要确认通用回答。"}]
        )

    def general(request: ChatRequest, stage_callback) -> ChatResponse:
        """模拟接受确认后只调用通用回答生成模型。"""
        calls["general"] += 1
        general_done.set()
        return ChatResponse("general", [{"type": "text", "text": "通用回答"}])

    manager = ChatTaskManager(_settings(tmp_path), store, insufficient, general)
    manager.start()
    submitted = manager.submit(ChatRequest("资料问题"))
    _wait_for(lambda: store.get(submitted.chat_id).status is ChatStatus.NEEDS_CONFIRMATION)

    manager.confirm(
        submitted.chat_id,
        accept_general_answer=True,
        request=ChatRequest("资料问题", b"original-image", "image/png"),
    )
    assert general_done.wait(1)
    _wait_for(lambda: store.get(submitted.chat_id).status is ChatStatus.COMPLETED)
    manager.stop()

    assert calls == {"retrieval": 1, "general": 1}
    assert store.get(submitted.chat_id).response.route == "general"


def test_confirmation_rejects_invalid_state_and_unknown_id(tmp_path) -> None:
    """未知 chat_id 返回未找到错误，非确认态重复提交返回冲突错误。"""
    store = MemoryChatStatusStore()
    manager = ChatTaskManager(
        _settings(tmp_path), store,
        lambda request, callback: ChatResponse("general"),
        lambda request, callback: ChatResponse("general"),
    )
    manager.start()
    with pytest.raises(ChatNotFoundError):
        manager.confirm("missing", accept_general_answer=False)
    record = manager.submit(ChatRequest("question"))
    with pytest.raises(ChatConflictError):
        manager.confirm(record.chat_id, accept_general_answer=False)
    manager.stop()


def test_rejecting_general_answer_marks_waiting_task_cancelled(tmp_path) -> None:
    """用户拒绝通用回答后任务进入 cancelled，且不会执行回答处理器。"""
    store = MemoryChatStatusStore()
    completed = Event()

    def insufficient(request, stage_callback):
        """为任务创建可确认的证据不足终态。"""
        stage_callback(ChatStage.AWAITING_CONFIRMATION, 0)
        return ChatResponse("needs_confirmation")

    def unexpected_general(request, stage_callback):
        """验证拒绝路径不会调用通用回答生成器。"""
        completed.set()
        raise AssertionError("拒绝后不应生成回答")

    manager = ChatTaskManager(
        _settings(tmp_path), store, insufficient, unexpected_general
    )
    manager.start()
    submitted = manager.submit(ChatRequest("需要确认的问题"))
    _wait_for(lambda: store.get(submitted.chat_id).status is ChatStatus.NEEDS_CONFIRMATION)

    cancelled = manager.confirm(submitted.chat_id, accept_general_answer=False)
    manager.stop()

    assert cancelled.status is ChatStatus.CANCELLED
    assert not completed.is_set()


def test_redis_status_write_error_marks_task_failed_when_recovery_write_succeeds(tmp_path) -> None:
    """阶段状态写入遇到一次 Redis 错误后记录安全 failed 状态。"""

    class OneFailureStatusStore(MemoryChatStatusStore):
        """只让第一条 worker 阶段更新失败，之后允许失败状态写入。"""

        def __init__(self) -> None:
            """启用一次性保存故障开关。"""
            super().__init__()
            self.fail_next_save = False

        def save(self, record: ChatStatusRecord) -> None:
            """按开关模拟 Redis 命令失败。"""
            with self._lock:
                if self.fail_next_save:
                    self.fail_next_save = False
                    raise ConnectionError("redis offline")
            super().save(record)

    store = OneFailureStatusStore()

    def trigger_status_update(request, stage_callback) -> ChatResponse:
        """触发 worker 的首次运行阶段状态持久化。"""
        stage_callback(ChatStage.SCOPE_AND_REWRITE, 0)
        return ChatResponse("general")

    manager = ChatTaskManager(
        _settings(tmp_path),
        store,
        trigger_status_update,
        trigger_status_update,
    )
    manager.start()
    submitted = manager.submit(ChatRequest("Redis 故障测试"))
    store.fail_next_save = True

    _wait_for(lambda: store.get(submitted.chat_id).status is ChatStatus.FAILED)
    manager.stop()

    assert store.get(submitted.chat_id).error == "问答处理失败，请重新提交。"


def test_transient_initial_redis_read_error_is_retried_and_marked_failed(tmp_path) -> None:
    """worker 初始读取状态遇到短暂故障时重读一次并保存 failed。"""

    class OneFailureReadStore(MemoryChatStatusStore):
        """只让 worker 第一次读取状态时失败的内存替身。"""

        def __init__(self) -> None:
            """初始化一次性读取故障开关。"""
            super().__init__()
            self.fail_next_get = False
            self.read_failed = Event()

        def get(self, chat_id: str) -> ChatStatusRecord | None:
            """在开关启用时模拟 Redis 暂时不可用。"""
            with self._lock:
                if self.fail_next_get:
                    self.fail_next_get = False
                    self.read_failed.set()
                    raise ConnectionError("redis offline")
            return super().get(chat_id)

    store = OneFailureReadStore()
    manager = ChatTaskManager(
        _settings(tmp_path),
        store,
        lambda request, callback: ChatResponse("general"),
        lambda request, callback: ChatResponse("general"),
    )
    manager.start()
    store.fail_next_get = True
    submitted = manager.submit(ChatRequest("Redis 读取故障测试"))

    assert store.read_failed.wait(1)
    _wait_for(lambda: store.get(submitted.chat_id).status is ChatStatus.FAILED)
    manager.stop()

    assert store.get(submitted.chat_id).error == "问答状态读取失败，请重新提交。"


def test_chat_execution_log_is_jsonl_and_excludes_request_content(tmp_path) -> None:
    """每个阶段事件写为独立 JSON 行，日志仅含允许的运行元数据。"""
    logger = ChatExecutionLogger(tmp_path / "logs", max_bytes=1024, backup_count=2)

    logger.write(
        ChatExecutionEvent(
            chat_id="chat-1",
            stage=ChatStage.RETRIEVAL,
            event_type="completed",
            duration_ms=35,
            model_name="Qwen/Qwen3-VL",
            retrieved_count=5,
        )
    )

    line = (tmp_path / "logs" / "chat.jsonl").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["chat_id"] == "chat-1"
    assert parsed["stage"] == "retrieval"
    assert parsed["event_type"] == "completed"
    assert parsed["retrieved_count"] == 5
    assert "question" not in parsed
    assert "prompt" not in parsed


def test_chat_execution_logger_rotates_by_size_and_keeps_configured_backups(tmp_path) -> None:
    """超过配置文件大小后轮转 JSONL，并只保留指定数量的历史文件。"""
    logs_dir = tmp_path / "rotating"
    logger = ChatExecutionLogger(logs_dir, max_bytes=200, backup_count=2)
    for index in range(8):
        logger.write(
            ChatExecutionEvent(
                chat_id=f"chat-{index}",
                stage=ChatStage.ANSWER_GENERATION,
                event_type="completed",
                model_name="Qwen/Qwen3-VL",
                retrieved_count=5,
            )
        )
    logger.close()

    log_files = list(logs_dir.glob("chat.jsonl*"))
    assert len(log_files) == 3
    assert (logs_dir / "chat.jsonl.1").is_file()
    assert (logs_dir / "chat.jsonl.2").is_file()


def test_model_failure_persists_safe_error_and_does_not_log_exception_text(tmp_path) -> None:
    """模型异常会进入 failed，Redis 和 JSONL 均不包含异常中的用户正文。"""
    store = MemoryChatStatusStore()
    execution_logger = ChatExecutionLogger(tmp_path / "logs", 1024, 2)

    def fail_with_sensitive_error(request, stage_callback):
        """模拟模型异常文本意外包含敏感问题正文。"""
        stage_callback(ChatStage.ANSWER_GENERATION, 5)
        raise RuntimeError("敏感问题原文不应进入状态或日志")

    manager = ChatTaskManager(
        _settings(tmp_path),
        store,
        fail_with_sensitive_error,
        fail_with_sensitive_error,
        execution_logger=execution_logger,
    )
    manager.start()
    accepted = manager.submit(ChatRequest("正常提交的问题"))
    _wait_for(lambda: store.get(accepted.chat_id).status is ChatStatus.FAILED)
    manager.stop()

    log_text = (tmp_path / "logs" / "chat.jsonl").read_text(encoding="utf-8")
    assert "敏感问题原文" not in log_text
    assert "正常提交的问题" not in log_text
    assert store.get(accepted.chat_id).error == "问答处理失败，请重新提交。"
    assert store.get(accepted.chat_id).status is ChatStatus.FAILED
    failure_event = json.loads(log_text.splitlines()[-1])
    assert failure_event["event_type"] == "failed"
    assert failure_event["error_code"] == "RuntimeError"
