import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.domain.enums import Sentiment
from app.infra.db.models import Conversation, Message

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(min_length=1)


class SeedMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    sentiment: Sentiment | None = None
    created_at: datetime


class SeedConversation(BaseModel):
    id: UUID
    created_at: datetime
    messages: list[SeedMessage] = Field(min_length=1)


class SeedSummary(BaseModel):
    conversations: int
    messages: int


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 16, hour, minute, second, tzinfo=timezone.utc)


SEED_CONVERSATIONS = [
    SeedConversation(
        id=UUID("8f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"),
        created_at=_at(8, 59),
        messages=[
            SeedMessage(
                id=UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"),
                role="user",
                content="今天面试又没过，感觉自己很失败。",
                sentiment=Sentiment.NEGATIVE,
                created_at=_at(9, 0),
            ),
            SeedMessage(
                id=UUID("b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"),
                role="assistant",
                content="面试失利确实让人沮丧，但这只是一次经历，不代表你的能力。",
                sentiment=Sentiment.NEGATIVE,
                created_at=_at(9, 0, 5),
            ),
            SeedMessage(
                id=UUID("c3d4e5f6-a7b8-4c9d-8e1f-2a3b4c5d6e7f"),
                role="user",
                content="谢谢你，我打算今晚复盘一下。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(9, 1),
            ),
            SeedMessage(
                id=UUID("d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70"),
                role="assistant",
                content="愿意复盘已经是很好的开始，先照顾好自己的情绪，再一步步来。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(9, 1, 5),
            ),
        ],
    ),
    SeedConversation(
        id=UUID("9a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"),
        created_at=_at(9, 59),
        messages=[
            SeedMessage(
                id=UUID("e5f6a7b8-c9d0-4e1f-8a2b-3c4d5e6f7a8b"),
                role="user",
                content="我刚完成了一个拖延很久的小项目，想分享这份开心。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(10, 0),
            ),
            SeedMessage(
                id=UUID("f6a7b8c9-d0e1-4f2a-9b3c-4d5e6f7a8b9c"),
                role="assistant",
                content="听到这个消息真为你开心，完成拖延已久的事需要不少坚持。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(10, 0, 5),
            ),
            SeedMessage(
                id=UUID("a7b8c9d0-e1f2-4a3b-8c4d-5e6f7a8b9c0d"),
                role="user",
                content="是的，我准备给自己放个假。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(10, 1),
            ),
            SeedMessage(
                id=UUID("b8c9d0e1-f2a3-4b4c-9d5e-6f7a8b9c0d1e"),
                role="assistant",
                content="好好休息也是认真生活的一部分，祝你有轻松的一天。",
                sentiment=Sentiment.POSITIVE,
                created_at=_at(10, 1, 5),
            ),
        ],
    ),
]


def _business_tables_exist(connection) -> bool:
    inspector = inspect(connection)
    return inspector.has_table("conversations") and inspector.has_table("messages")


async def seed(database_url: str) -> SeedSummary:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            if not await connection.run_sync(_business_tables_exist):
                raise RuntimeError("业务表不存在，请先执行 alembic upgrade head")

        async with AsyncSession(engine) as session:
            async with session.begin():
                for conversation in SEED_CONVERSATIONS:
                    await session.execute(
                        pg_insert(Conversation)
                        .values(id=conversation.id, created_at=conversation.created_at)
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                    await session.execute(
                        pg_insert(Message)
                        .values(
                            [
                                {
                                    "id": message.id,
                                    "conversation_id": conversation.id,
                                    "role": message.role,
                                    "content": message.content,
                                    "sentiment": (
                                        message.sentiment.value if message.sentiment else None
                                    ),
                                    "created_at": message.created_at,
                                }
                                for message in conversation.messages
                            ]
                        )
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
    finally:
        await engine.dispose()

    return SeedSummary(
        conversations=len(SEED_CONVERSATIONS),
        messages=sum(len(conversation.messages) for conversation in SEED_CONVERSATIONS),
    )


async def main() -> None:
    settings = SeedSettings()
    try:
        summary = await seed(settings.database_url)
    except RuntimeError as error:
        raise SystemExit(f"seed failed: {error}") from error

    print(json.dumps(summary.model_dump(), ensure_ascii=False))
    for conversation in SEED_CONVERSATIONS:
        print(f"{conversation.id}  {len(conversation.messages)} messages")


if __name__ == "__main__":
    asyncio.run(main())
