"""FastAPI 入口：路由装配、启动时建表/建 topic/启动状态事件网关、托管前端静态页。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from ..config import settings
from ..db import init_db, session_scope
from ..kafka_bus import ensure_topics, get_producer
from ..models import Chunk, Document
from ..status import STATUS_LABELS
from . import routes_assets, routes_chat, routes_documents, routes_events
from .events import hub, status_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rag.api")

FRONTEND_DIR = settings.project_root / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    hub.attach_loop(asyncio.get_running_loop())
    try:
        topics = ensure_topics()
        logger.info("Kafka topics：%s", topics)
    except Exception as exc:
        logger.warning("Kafka 暂时不可用（%s: %s），上传会返回明确的失败状态", type(exc).__name__, exc)
    status_consumer.start()
    logger.info(
        "API 启动：parser=%s embed=%s store=%s llm=%s",
        settings.parser_backend,
        settings.embed_backend,
        settings.vector_store,
        settings.llm_model if settings.llm_api_key else "mock(未配置 LLM_API_KEY)",
    )
    try:
        yield
    finally:
        status_consumer.stop()


app = FastAPI(
    title="多模态 RAG 问答（Kafka 版）",
    description="上传 PDF -> Kafka -> MinerU 解析 -> 向量化 -> 图文混排问答",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_documents.router)
app.include_router(routes_chat.router)
app.include_router(routes_assets.router)
app.include_router(routes_events.router)


@app.get("/api/health", tags=["system"])
def health() -> dict:
    kafka_ok = True
    kafka_error = None
    try:
        get_producer().list_topics(timeout=3)
    except Exception as exc:
        kafka_ok = False
        kafka_error = str(exc)
    return {
        "status": "ok",
        "kafka": {"bootstrap": settings.kafka_bootstrap, "ok": kafka_ok, "error": kafka_error},
        "parser": settings.parser_backend,
        "embed": settings.embed_backend,
        "vector_store": settings.vector_store,
        "llm": settings.llm_model if settings.llm_api_key else "mock",
    }


@app.get("/api/system", tags=["system"])
def system() -> dict:
    with session_scope() as session:
        doc_total = int(session.execute(select(func.count()).select_from(Document)).scalar_one())
        doc_completed = int(
            session.execute(
                select(func.count()).select_from(Document).where(Document.status == "completed")
            ).scalar_one()
        )
        chunk_total = int(session.execute(select(func.count()).select_from(Chunk)).scalar_one())

    return {
        "kafka": {
            "bootstrap": settings.kafka_bootstrap,
            "request_topic": settings.topic_parse_request,
            "status_topic": settings.topic_parse_status,
            "dlq_topic": settings.topic_parse_dlq,
            "worker_group": settings.worker_group,
        },
        "parser": settings.parser_backend,
        "mineru_backend": settings.mineru_backend,
        "embed": settings.embed_backend,
        "text_model": settings.text_embed_model,
        "clip_model": settings.clip_model,
        "vector_store": settings.vector_store,
        "milvus_collection": settings.milvus_collection if settings.vector_store == "milvus" else None,
        "llm_backend": "mock" if not settings.llm_api_key else "openai-compatible",
        "llm_model": settings.llm_model,
        "chunk_size": settings.chunk_size,
        "top_k_text": settings.top_k_text,
        "top_k_image": settings.top_k_image,
        "status_labels": STATUS_LABELS,
        "stats": {
            "documents": doc_total,
            "completed": doc_completed,
            "chunks": chunk_total,
        },
    }


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover - 仅提示，不影响 API
    logger.warning("未找到前端目录：%s", FRONTEND_DIR)
