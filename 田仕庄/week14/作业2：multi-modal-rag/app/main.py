"""FastAPI 装配入口。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import chat, documents, health, kb
from app.config import get_settings
from app.db import init_db
from app.errors import AppError
from app.ingest import sweeper
from app.logging_setup import setup_logging, shutdown_logging
from app.middleware import TraceMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    setup_logging("app")
    settings.ensure_dirs()
    init_db()
    logger.info(
        "app started: version=%s mock=%s parse=%s data=%s",
        __version__,
        settings.mock_external,
        settings.parse_backend,
        settings.data_dir,
    )

    sweep_stop = asyncio.Event()
    sweep_task: asyncio.Task | None = None
    if settings.sweep_enabled:
        sweep_task = asyncio.create_task(sweeper.run_forever(sweep_stop))

    try:
        yield
    finally:
        if sweep_task is not None:
            sweep_stop.set()
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
        logger.info("app stopped")
        shutdown_logging()


app = FastAPI(
    title="Multimodal RAG API",
    description="批量 PDF 上传、Kafka 异步解析、状态追踪、多模态问答",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(TraceMiddleware)
app.include_router(health.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(kb.router)


@app.exception_handler(AppError)
async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


settings = get_settings()
os.makedirs(settings.uploads_dir, exist_ok=True)
os.makedirs(settings.processed_dir, exist_ok=True)
app.mount(
    "/files/uploads",
    StaticFiles(directory=str(settings.uploads_dir)),
    name="uploads",
)
app.mount(
    "/files/processed",
    StaticFiles(directory=str(settings.processed_dir)),
    name="processed",
)
if settings.frontend_dir.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(settings.frontend_dir), html=True),
        name="frontend",
    )
else:
    logger.warning("frontend dir not found: %s", settings.frontend_dir)
