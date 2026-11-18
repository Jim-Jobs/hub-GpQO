import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

SessionFactory = async_sessionmaker[AsyncSession]


def create_db_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_database(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))


if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        dashscope_api_key="demo-key",
        database_url="postgresql+asyncpg://app:app@localhost:5432/sentiment_chat",
    )
    engine = create_db_engine(settings)
    print("engine:", engine.url.render_as_string(hide_password=True))
    asyncio.run(engine.dispose())
