from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_serializer

from app.domain.enums import Sentiment


class MessageItem(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    sentiment: Sentiment | None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MessageListResponse(BaseModel):
    conversation_id: UUID
    messages: list[MessageItem]


if __name__ == "__main__":
    item = MessageItem(
        id="3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c",
        role="user",
        content="今天面试没过。",
        sentiment=None,
        created_at=datetime.now(timezone.utc),
    )
    print(item.model_dump(mode="json"))
