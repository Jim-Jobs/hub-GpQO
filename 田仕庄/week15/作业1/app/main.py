import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# psycopg 的异步模式在 Windows 上不支持 ProactorEventLoop。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.errors import install_error_handlers
from app.api.routes import chat, conversation, health
from app.config import Settings
from app.engines.factory import create_pipeline
from app.infra.db.session import create_db_engine, create_session_factory
from app.infra.logging import configure_logging
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService
from app.services.locks import ConversationLocks

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(resolved.log_level)
        db_engine: AsyncEngine = create_db_engine(resolved)
        session_factory = create_session_factory(db_engine)
        pipeline = await create_pipeline(resolved, db_engine)

        app.state.settings = resolved
        app.state.db_engine = db_engine
        app.state.session_factory = session_factory
        app.state.pipeline = pipeline
        app.state.chat_service = ChatService(
            session_factory, pipeline, resolved, ConversationLocks()
        )
        app.state.conversation_service = ConversationService(session_factory)
        try:
            yield
        finally:
            await pipeline.aclose()
            await db_engine.dispose()

    app = FastAPI(
        title="Sentiment Chat",
        description="FastAPI + Qwen 情感对话服务，支持双编排引擎。",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.include_router(chat.router)
    app.include_router(conversation.router)
    app.include_router(health.router)
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # uvicorn 0.36+ 在 Windows 上显式选择 ProactorEventLoop，loop="none" 才沿用上方的 Selector 策略。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False, loop="none")
