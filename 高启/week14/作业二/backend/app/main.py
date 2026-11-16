"""装配 FastAPI 依赖，并在应用生命周期中管理文档与问答 worker。"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.agents.chat.agent import ChatAgent
from backend.app.agents.chat.state import ChatState
from backend.app.api.assets import create_asset_router
from backend.app.api.chat import create_chat_router
from backend.app.api.document_status import create_document_status_router
from backend.app.api.document_upload import create_document_upload_router
from backend.app.chat.application.task_manager import ChatTaskManager, StageCallback
from backend.app.chat.domain.models import ChatImage, ChatRequest, ChatResponse
from backend.app.core.config import Settings
from backend.app.documents.application.upload_documents import UploadDependencies
from backend.app.documents.indexing.pipeline import DocumentIndexer
from backend.app.integrations.redis_chat_status_store import RedisChatStatusStore
from backend.app.integrations.redis_document_store import RedisDocumentStore


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@dataclass
class AppRuntime:
    """保存应用共享依赖，并控制问答、Kafka 和连接池生命周期。"""

    settings: Settings
    document_store: RedisDocumentStore
    upload_dependencies: UploadDependencies
    chat_manager: ChatTaskManager
    consumer_manager: Any
    redis_client: Any
    kafka_producer: Any

    def start(self) -> None:
        """启动问答任务 worker 和 PDF Kafka consumers。"""
        try:
            self.chat_manager.start()
            self.consumer_manager.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        """按依赖顺序停止 worker 并关闭 Kafka、Redis 客户端。"""
        try:
            self.consumer_manager.stop()
        finally:
            try:
                self.chat_manager.stop()
            finally:
                self.kafka_producer.close()
                self.redis_client.close()


def create_app(settings: Settings | None = None, *, runtime: Any | None = None) -> FastAPI:
    """创建 HTTP 应用；生产环境组装服务，测试可注入隔离运行依赖。"""
    app_settings = settings or Settings.load()
    app_runtime = runtime if runtime is not None else _build_runtime(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """在服务启动/退出时成对启动和关闭后台任务与客户端。"""
        app_runtime.start()
        try:
            yield
        finally:
            app_runtime.stop()

    app = FastAPI(title="多模态 RAG API", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.runtime = app_runtime
    app.include_router(create_document_upload_router(app_runtime.upload_dependencies))
    app.include_router(create_document_status_router(app_runtime.document_store))
    app.include_router(
        create_asset_router(app_settings.parsed_dir, app_runtime.document_store)
    )
    app.include_router(
        create_chat_router(lambda: app_runtime.chat_manager, app_settings.max_chat_image_bytes)
    )
    _mount_frontend(app)
    return app


def create_frontend_preview_app() -> FastAPI:
    """创建无需 Redis、Kafka 或模型配置的静态前端预览应用。"""
    app = FastAPI(title="多模态 RAG 前端预览")
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """将静态文件和首页挂载到 FastAPI 应用。"""
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="frontend-static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """返回作为浏览器入口的原生单页 HTML。"""
        return FileResponse(FRONTEND_DIR / "index.html")


def _build_runtime(settings: Settings) -> AppRuntime:
    """构造真实 Redis、Kafka、文档索引器和问答 Agent 共享依赖。"""
    settings.validate_required_services()
    settings.initialize_data_directories()

    try:
        from kafka import KafkaProducer
        from redis import Redis
        from backend.app.integrations.kafka_producer import KafkaIngestProducer
        from backend.app.workers.consumer_manager import ConsumerManager
    except ImportError as error:
        raise RuntimeError("缺少 Kafka/Redis Python 客户端依赖") from error

    redis_client = None
    kafka_client = None
    try:
        redis_client = Redis.from_url(settings.redis_url)
        kafka_client = KafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        document_store = RedisDocumentStore(redis_client)
        publisher = KafkaIngestProducer(kafka_client, settings.kafka_topic)
        chat_status_store = RedisChatStatusStore(
            redis_client, ttl_seconds=settings.chat_status_ttl_seconds
        )
        indexer = DocumentIndexer(settings)
        agent = ChatAgent.from_settings(settings, document_store)

        def process_chat(request: ChatRequest, on_stage: StageCallback) -> ChatResponse:
            """读取资料库概览并执行带阶段回调的 ChatAgent 问答。"""
            profile = document_store.get_corpus_profile()
            user_image = _chat_image(request)
            state = ChatState(
                request.question_text,
                profile.summary if profile is not None else "",
                user_image=user_image,
            )
            return agent.run(state, stage_callback=on_stage)

        def process_general(request: ChatRequest, _on_stage: StageCallback) -> ChatResponse:
            """继续确认后的通用回答；不重新读取资料库或执行检索。"""
            return agent.answer_general(request.question_text, _chat_image(request))

        chat_manager = ChatTaskManager(
            settings,
            chat_status_store,
            process_chat,
            process_general,
        )
        consumer_manager = ConsumerManager(
            settings,
            document_store,
            indexer,
        )
        return AppRuntime(
            settings,
            document_store,
            UploadDependencies(settings, document_store, publisher),
            chat_manager,
            consumer_manager,
            redis_client,
            kafka_client,
        )
    except Exception:
        if kafka_client is not None:
            kafka_client.close()
        if redis_client is not None:
            redis_client.close()
        raise


def _chat_image(request: ChatRequest) -> ChatImage | None:
    """将 API 请求中暂存的图像字节包装为模型可传递对象。"""
    if request.image is None:
        return None
    return ChatImage(request.image, request.image_media_type or "")


def main() -> None:
    """解析 Web 监听参数并启动 Uvicorn 应用工厂。"""
    import uvicorn

    parser = argparse.ArgumentParser(description="启动多模态 RAG FastAPI 服务")
    parser.add_argument("--host", default=os.getenv("RAG_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("RAG_WEB_PORT", "8000")))
    parser.add_argument(
        "--frontend-preview",
        action="store_true",
        help="仅提供前端静态页面，不连接 Redis、Kafka 或模型服务",
    )
    arguments = parser.parse_args()
    if arguments.frontend_preview:
        uvicorn.run(
            create_frontend_preview_app(),
            host=arguments.host,
            port=arguments.port,
        )
        return
    uvicorn.run(
        "backend.app.main:create_app",
        factory=True,
        host=arguments.host,
        port=arguments.port,
    )


if __name__ == "__main__":
    main()
