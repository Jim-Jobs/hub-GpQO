from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Sentiment
from app.domain.errors import conversation_not_found_error, invalid_cursor_error
from app.infra.db.models import Conversation, Message
from app.infra.db.session import SessionFactory
from app.schemas.conversation import MessageItem, MessageListResponse


class ConversationService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def list_messages(
        self, conversation_id: UUID, limit: int, before: UUID | None
    ) -> MessageListResponse:
        async with self._session_factory() as session:
            exists = await session.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id)
            )
            if exists is None:
                raise conversation_not_found_error()

            statement = select(Message).where(Message.conversation_id == conversation_id)
            if before is not None:
                cursor = await self._find_cursor(session, conversation_id, before)
                statement = statement.where(
                    or_(
                        Message.created_at < cursor.created_at,
                        (Message.created_at == cursor.created_at) & (Message.id < cursor.id),
                    )
                )

            statement = statement.order_by(
                Message.created_at.desc(), Message.id.desc()
            ).limit(limit)
            rows = list((await session.scalars(statement)).all())
            rows.reverse()
            return MessageListResponse(
                conversation_id=conversation_id,
                messages=[self._to_item(row) for row in rows],
            )

    @staticmethod
    async def _find_cursor(
        session: AsyncSession, conversation_id: UUID, message_id: UUID
    ) -> Message:
        cursor = await session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
            )
        )
        if cursor is None:
            raise invalid_cursor_error()
        return cursor

    @staticmethod
    def _to_item(message: Message) -> MessageItem:
        return MessageItem(
            id=message.id,
            role=message.role,
            content=message.content,
            sentiment=Sentiment(message.sentiment) if message.sentiment else None,
            created_at=message.created_at,
        )


if __name__ == "__main__":
    print(ConversationService.__doc__ or "ConversationService")
    print("history order: created_at asc, id asc")
    print("cursor:", isinstance(datetime.now(), datetime))
