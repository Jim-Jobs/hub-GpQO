from types import TracebackType

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import Settings


class CheckpointerLifecycle:
    """持有 AsyncPostgresSaver 的异步上下文，由应用 lifespan 控制生命周期。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._manager = None
        self.saver: AsyncPostgresSaver | None = None

    async def __aenter__(self) -> AsyncPostgresSaver:
        self._manager = AsyncPostgresSaver.from_conn_string(
            self._settings.psycopg_database_url
        )
        self.saver = await self._manager.__aenter__()
        await self.saver.setup()
        return self.saver

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._manager is not None:
            await self._manager.__aexit__(exc_type, exc, traceback)
        self._manager = None
        self.saver = None


if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        dashscope_api_key="demo-key",
        database_url="postgresql+asyncpg://app:app@localhost:5432/sentiment_chat",
    )
    print("checkpointer url:", settings.psycopg_database_url)
