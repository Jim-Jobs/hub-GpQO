from uuid import UUID

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import Sentiment


class ChatRequest(BaseModel):
    conversation_id: UUID
    message: str

    @field_validator("message")
    @classmethod
    def trim_and_reject_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise PydanticCustomError("empty_input", "输入不能为空。")
        return trimmed


class ChatResponse(BaseModel):
    input: str
    sentiment: Sentiment
    reply: str
    conversation_id: UUID


if __name__ == "__main__":
    request = ChatRequest(conversation_id="3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c", message="  你好  ")
    response = ChatResponse(
        input=request.message,
        sentiment=Sentiment.POSITIVE,
        reply="听到好消息真为你开心。",
        conversation_id=request.conversation_id,
    )
    print(request.model_dump(mode="json"))
    print(response.model_dump(mode="json"))
