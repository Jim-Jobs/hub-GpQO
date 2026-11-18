from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.enums import Sentiment
from app.schemas.chat import ChatRequest, ChatResponse


def test_chat_request_trims_message():
    request = ChatRequest(conversation_id=uuid4(), message="  你好  ")
    assert request.message == "你好"


@pytest.mark.parametrize("message", ["", "   "])
def test_chat_request_rejects_empty_message(message):
    with pytest.raises(ValidationError):
        ChatRequest(conversation_id=uuid4(), message=message)


def test_chat_request_rejects_invalid_conversation_id():
    with pytest.raises(ValidationError):
        ChatRequest(conversation_id="not-a-uuid", message="你好")


def test_chat_response_serializes_sentiment_as_string():
    response = ChatResponse(
        input="你好",
        sentiment=Sentiment.NEGATIVE,
        reply="别灰心。",
        conversation_id=uuid4(),
    )
    payload = response.model_dump(mode="json")
    assert payload["sentiment"] == "negative"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
