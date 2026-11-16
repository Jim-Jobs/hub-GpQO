"""覆盖问答 HTTP 接口的入队、状态轮询和上传/队列错误边界。"""

from threading import Event, Lock
from time import monotonic, sleep

from fastapi import HTTPException, UploadFile
import pytest

from backend.app.api.chat import create_chat_router
from backend.app.chat.application.task_manager import ChatTaskManager
from backend.app.chat.domain.models import ChatResponse
from backend.app.chat.domain.enums import ChatStatus
from backend.app.core.config import Settings


def _wait_for(predicate, timeout: float = 2) -> None:
    """等待后台任务状态变更，避免测试依赖固定时长 sleep。"""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    raise AssertionError("后台状态没有按预期变化")


class MemoryStatusStore:
    """为 HTTP 测试保存在内存中的任务状态记录。"""

    def __init__(self) -> None:
        """初始化任务记录字典。"""
        self.records = {}
        self._lock = Lock()

    def save(self, record) -> None:
        """按 chat_id 覆盖状态记录。"""
        with self._lock:
            self.records[record.chat_id] = record

    def get(self, chat_id):
        """读取状态，不存在时返回 None。"""
        with self._lock:
            return self.records.get(chat_id)

    def fail_interrupted(self) -> int:
        """HTTP 用例没有上次进程遗留状态。"""
        return 0


def _endpoint(manager: ChatTaskManager, path: str):
    """从路由集合取出 endpoint，避免 Windows socketpair 依赖的 TestClient。"""
    router = create_chat_router(lambda: manager, max_image_bytes=4)
    return next(route.endpoint for route in router.routes if route.path == path)


def _complete_coroutine(coroutine):
    """运行只包含立即完成 await 的路由协程，不创建系统事件循环。"""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("测试 endpoint 意外挂起并依赖事件循环")


def _upload(filename: str, content: bytes, media_type: str) -> UploadFile:
    """创建只用于接口逻辑测试的图片文件替身，避免 anyio 线程池。"""
    upload = UploadFile(filename=filename, file=None, headers={"content-type": media_type})

    async def read(size: int = -1) -> bytes:
        """按指定大小从固定载荷返回内容。"""
        return content[:size]

    upload.read = read
    return upload


def _settings(tmp_path, capacity: int = 2) -> Settings:
    """创建带小队列容量的临时问答配置。"""
    return Settings.from_env(
        {"RAG_DATA_ROOT": str(tmp_path), "RAG_CHAT_QUEUE_CAPACITY": str(capacity)}
    )


def test_chat_api_returns_accepted_then_pollable_completed_result(tmp_path) -> None:
    """POST 立即返回 chat_id，GET 随后返回可轮询的最终答案。"""
    release = Event()
    started = Event()
    store = MemoryStatusStore()

    def process(request, stage_callback):
        """暂停模型替身以稳定观察 accepted/running 到 completed。"""
        from backend.app.chat.domain.enums import ChatStage

        stage_callback(ChatStage.SCOPE_AND_REWRITE, 0)
        started.set()
        assert release.wait(2)
        return ChatResponse("out_of_scope", [{"type": "text", "text": "完成答案"}])

    manager = ChatTaskManager(_settings(tmp_path), store, process, process)
    manager.start()
    try:
        accepted = _complete_coroutine(
            _endpoint(manager, "/api/chat")("问一个问题", None)
        )
        assert accepted.status == "accepted"
        assert started.wait(1)
        assert _endpoint(manager, "/api/chat/{chat_id}")(accepted.chat_id).status in {
            ChatStatus.ACCEPTED,
            ChatStatus.RUNNING,
        }
        release.set()
        _wait_for(lambda: store.get(accepted.chat_id).status is ChatStatus.COMPLETED)
        completed = _endpoint(manager, "/api/chat/{chat_id}")(accepted.chat_id)
    finally:
        release.set()
        manager.stop()

    assert completed.status is ChatStatus.COMPLETED
    assert completed.response.answer_segments[0]["text"] == "完成答案"


def test_chat_api_rejects_oversized_or_non_image_payload_before_enqueue(tmp_path) -> None:
    """超限图片返回 413、非图片 MIME 返回 415，且都不创建任务状态。"""
    store = MemoryStatusStore()
    manager = ChatTaskManager(
        _settings(tmp_path), store,
        lambda request, callback: ChatResponse("general"),
        lambda request, callback: ChatResponse("general"),
    )
    manager.start()
    try:
        endpoint = _endpoint(manager, "/api/chat")
        with pytest.raises(HTTPException) as too_large:
            _complete_coroutine(
                endpoint("问题", _upload("big.png", b"12345", "image/png"))
            )
        with pytest.raises(HTTPException) as wrong_type:
            _complete_coroutine(
                endpoint("问题", _upload("file.pdf", b"123", "application/pdf"))
            )
    finally:
        manager.stop()

    assert too_large.value.status_code == 413
    assert wrong_type.value.status_code == 415
    assert store.records == {}


def test_chat_api_maps_full_queue_to_http_429_without_orphan_record(tmp_path) -> None:
    """队列满时 API 返回 429，接受状态只属于已实际入队的任务。"""
    blocked = Event()
    entered = Event()
    store = MemoryStatusStore()

    def process(request, stage_callback):
        """阻塞 worker 以填满一个等待槽。"""
        entered.set()
        assert blocked.wait(2)
        return ChatResponse("general")

    manager = ChatTaskManager(_settings(tmp_path, capacity=1), store, process, process)
    manager.start()
    try:
        endpoint = _endpoint(manager, "/api/chat")
        first = _complete_coroutine(endpoint("运行中", None))
        assert entered.wait(1)
        second = _complete_coroutine(endpoint("等待中", None))
        try:
            _complete_coroutine(endpoint("应拒绝", None))
        except HTTPException as error:
            rejected = error
    finally:
        blocked.set()
        manager.stop()

    assert second.status == "accepted"
    assert rejected.status_code == 429
    assert len(store.records) == 2


def test_chat_api_returns_404_for_unknown_id_and_409_for_invalid_confirmation(tmp_path) -> None:
    """未知状态查询返回 404，非等待确认任务的确认请求返回 409。"""
    manager = ChatTaskManager(
        _settings(tmp_path),
        MemoryStatusStore(),
        lambda request, callback: ChatResponse("general"),
        lambda request, callback: ChatResponse("general"),
    )
    manager.start()
    try:
        get_status = _endpoint(manager, "/api/chat/{chat_id}")
        confirm = _endpoint(manager, "/api/chat/{chat_id}/confirmation")
        with pytest.raises(HTTPException) as unknown:
            get_status("missing")
        accepted = _complete_coroutine(_endpoint(manager, "/api/chat")("question", None))
        with pytest.raises(HTTPException) as conflict:
            _complete_coroutine(confirm(accepted.chat_id, False, None, None))
    finally:
        manager.stop()

    assert unknown.value.status_code == 404
    assert conflict.value.status_code == 409
