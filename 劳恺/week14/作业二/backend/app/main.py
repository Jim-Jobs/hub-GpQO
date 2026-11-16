"""FastAPI 入口。

对应文档：``docs/01-架构设计.md`` §2.2、``docs/04-API契约.md``。

API 进程只负责：上传受理、文档 CRUD、状态 SSE、问答 SSE、会话。
**不做解析、不做向量化** —— 那些在 Worker 进程里。解析是分钟级慢任务，
与问答的毫秒级延迟要求冲突，同进程会互相堵。

本进程内还有一个后台协程 ``status-bus``：消费 ``pdf.parse.status`` 并扇出给
所有 SSE 订阅者。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.documents import static_router
from app.api.errors import register_error_handlers
from app.api.sessions import router as sessions_router
from app.config import Settings, get_settings
from app.kafka.status_bus import StatusBus
from app.services.health import UP, check_dependencies
from app.storage.es_client import ESClient

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_producer(settings: Settings) -> Any:
    """构造 Kafka 生产者。失败返回 ``None`` —— 上传接口会据此返回 503。"""
    try:
        from app.kafka.client import KafkaProducerClient

        return KafkaProducerClient(settings)
    except Exception:  # noqa: BLE001 —— Kafka 暂时不可用不应阻止 API 启动
        logger.exception("Kafka 生产者初始化失败，上传接口将返回 503")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.ensure_data_dirs()

    es_client = app.state.es_client or ESClient(settings)
    app.state.es_client = es_client
    try:
        created = await es_client.ensure_indices()
        if created:
            logger.info("ES 索引就绪：%s", ", ".join(created))
        else:
            logger.info("ES 索引已存在，跳过创建")
    except Exception:  # noqa: BLE001 —— ES 不可用不应阻止进程启动
        logger.exception("ES 索引初始化失败，健康检查将报 degraded")

    if app.state.producer is None:
        app.state.producer = _build_producer(settings)

    status_bus = StatusBus(settings)
    app.state.status_bus = status_bus
    try:
        # mock 模式下没有在跑的 Worker，不需要真的消费 Kafka
        await status_bus.start(use_kafka=not settings.mock_mode)
    except Exception:  # noqa: BLE001
        logger.exception("status-bus 启动失败，SSE 将收不到增量事件")

    try:
        yield
    finally:
        await status_bus.stop()
        producer = app.state.producer
        if producer is not None and hasattr(producer, "close"):
            await producer.close()
        await es_client.close()
        logger.info("API 已关闭")


def create_app(
    settings: Settings | None = None,
    *,
    es_client: Any = None,
    producer: Any = None,
) -> FastAPI:
    """构造应用。

    ``es_client`` / ``producer`` 可注入替身，便于单测不依赖真实 ES / Kafka。
    """
    app = FastAPI(
        title="多模态 RAG 问答系统",
        description="基于 Kafka 的多模态 RAG 问答后端",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings or get_settings()
    app.state.es_client = es_client
    app.state.producer = producer
    app.state.status_bus = None

    register_error_handlers(app)
    app.include_router(documents_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(static_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, object]:
        """依赖健康检查。任一依赖 down 时 status=degraded，但 HTTP 仍为 200。"""
        current: Settings = app.state.settings
        states = await check_dependencies(current)
        degraded = any(state != UP for state in states.values())
        return {
            "status": "degraded" if degraded else "ok",
            **states,
            "mock_mode": current.mock_mode,
        }

    return app


app = create_app()
