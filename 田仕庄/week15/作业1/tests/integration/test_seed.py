import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.infra.db.models import Conversation, Message
from scripts.seed import SEED_CONVERSATIONS, SeedSummary, seed


async def _count_seed_rows(database_url: str) -> tuple[int, int]:
    conversation_ids = [conversation.id for conversation in SEED_CONVERSATIONS]
    message_ids = [
        message.id for conversation in SEED_CONVERSATIONS for message in conversation.messages
    ]
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            conversation_count = await session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.id.in_(conversation_ids))
            )
            message_count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.id.in_(message_ids))
            )
        return conversation_count or 0, message_count or 0
    finally:
        await engine.dispose()


def test_seed_is_idempotent(migrated_db):
    first = asyncio.run(seed(migrated_db))
    second = asyncio.run(seed(migrated_db))

    assert first == SeedSummary(conversations=2, messages=8)
    assert second == first
    assert asyncio.run(_count_seed_rows(migrated_db)) == (2, 8)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
