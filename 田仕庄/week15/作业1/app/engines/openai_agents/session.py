import asyncio
from uuid import UUID

from agents.extensions.memory import SQLAlchemySession
from sqlalchemy.ext.asyncio import AsyncEngine


class AgentSessionFactory:
    """按 conversation_id 创建 Agents SDK 会话；表结构只在首次使用时创建。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._ready = False
        self._lock = asyncio.Lock()

    async def create(self, conversation_id: UUID) -> SQLAlchemySession:
        async with self._lock:
            create_tables = not self._ready
            session = SQLAlchemySession(
                session_id=str(conversation_id),
                engine=self._engine,
                create_tables=create_tables,
            )
            if create_tables:
                await session.get_items(1)
                self._ready = True
        return session


if __name__ == "__main__":
    print(AgentSessionFactory.__doc__)
