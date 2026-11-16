"""API 进程内的状态总线。

对应文档：``docs/03-Kafka设计.md`` §6、``docs/01-架构设计.md`` §3.2。

```
Worker ──produce pdf.parse.status──▶ Kafka ──consume──▶ status-bus
                                                            │
                                          ┌─────────────────┼─────────────────┐
                                          ▼                 ▼                 ▼
                                      SSE 订阅者A        SSE 订阅者B        SSE 订阅者C
```

**三条设计约束**

1. 每个 SSE 连接注册一个 ``asyncio.Queue``；bus 收到事件后遍历所有订阅者投递。
2. 单个订阅者队列满时**丢弃该订阅者的这条事件** —— 慢客户端不能拖垮 bus。
   反正前端下次 ``GET /api/documents`` 能拉回真相。
3. 事件可能被 Kafka 重复投递 → 按 ``(doc_id, updated_at)`` 去重。

状态真相永远在 ES 的 ``documents.status``，SSE 只是加速器。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from app.config import Settings
from app.kafka.client import KafkaConsumerClient, commit_offset, poll_once

logger = logging.getLogger(__name__)

#: 单个订阅者的队列容量。满了就丢事件（慢客户端不拖垮 bus）
QUEUE_SIZE = 64
#: 去重窗口：记住最近多少条事件签名
DEDUP_WINDOW = 512


class StatusBus:
    """把 Kafka 状态事件扇出给所有 SSE 订阅者。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._seen: dict[str, None] = {}
        self._task: asyncio.Task[None] | None = None
        self._consumer: KafkaConsumerClient | None = None
        self._stopping = False

    # ---------- 订阅 ----------

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        logger.debug("SSE 订阅者加入，当前 %d 个", len(self._subscribers))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)
        logger.debug("SSE 订阅者退出，当前 %d 个", len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ---------- 投递 ----------

    def publish(self, event: dict[str, Any]) -> None:
        """把事件投给所有订阅者。队列满则丢弃该订阅者的这一条。"""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "订阅者队列已满，丢弃事件 doc_id=%s（前端应重新拉取全量）",
                    event.get("doc_id"),
                )

    def _is_duplicate(self, event: dict[str, Any]) -> bool:
        key = f"{event.get('doc_id')}|{event.get('updated_at')}|{event.get('status')}"
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > DEDUP_WINDOW:
            # 简单 FIFO 淘汰
            for old in list(self._seen)[: len(self._seen) - DEDUP_WINDOW]:
                self._seen.pop(old, None)
        return False

    # ---------- 生命周期 ----------

    async def start(self, *, use_kafka: bool = True) -> None:
        """启动后台消费协程。

        ``use_kafka=False``（mock 模式）时只保留扇出能力，由 Worker 直接调用
        :meth:`publish`，不需要真正的 Kafka 消费组。
        """
        if not use_kafka:
            logger.info("status-bus 以本地模式启动（不消费 Kafka）")
            return
        if self._task is not None:
            return
        self._consumer = KafkaConsumerClient(
            self._settings,
            topic=self._settings.kafka_topic_status,
            group_id=self._settings.kafka_status_consumer_group,
        )
        self._task = asyncio.create_task(self._run(), name="status-bus")
        logger.info("status-bus 已启动，topic=%s", self._settings.kafka_topic_status)

    async def _run(self) -> None:
        assert self._consumer is not None
        while not self._stopping:
            try:
                messages = await poll_once(self._consumer, timeout_ms=1000)
            except Exception:  # noqa: BLE001 —— 消费循环不能因单次异常退出
                logger.exception("status-bus 轮询失败，1 秒后重试")
                await asyncio.sleep(1)
                continue

            for message in messages:
                event = message.value
                if isinstance(event, dict) and not self._is_duplicate(event):
                    self.publish(event)
            if messages:
                try:
                    await commit_offset(self._consumer)
                except Exception:  # noqa: BLE001
                    logger.exception("status-bus 提交 offset 失败")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._consumer is not None:
            await asyncio.to_thread(self._consumer.close)
            self._consumer = None
        for queue in list(self._subscribers):
            await _drain_and_close(queue)
        self._subscribers.clear()


async def _drain_and_close(queue: asyncio.Queue) -> None:
    """给订阅者塞一个哨兵，让它退出循环。队列满时直接丢弃。"""
    with suppress(asyncio.QueueFull):
        queue.put_nowait(None)  # type: ignore[arg-type]


async def iter_events(queue: asyncio.Queue[dict[str, Any] | None]) -> AsyncIterator[dict[str, Any]]:
    """从订阅队列读事件，``None`` 表示总线关闭。"""
    while True:
        event = await queue.get()
        if event is None:
            return
        yield event
