from uuid import uuid4

import pytest


def _conversation_id() -> str:
    return str(uuid4())


def test_chat_returns_trimmed_input_sentiment_and_reply(client, fake_llm):
    fake_llm.outputs = ["negative"]
    conversation_id = _conversation_id()

    response = client.post(
        "/chat",
        json={"conversation_id": conversation_id, "message": "  今天面试又没过  "},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["input"] == "今天面试又没过"
    assert body["sentiment"] == "negative"
    assert body["reply"].strip()


def test_chat_identifies_positive_sentiment(client, fake_llm):
    fake_llm.outputs = ["positive"]

    response = client.post("/chat", json={"conversation_id": _conversation_id(), "message": "升职了！"})

    assert response.status_code == 200
    assert response.json()["sentiment"] == "positive"


@pytest.mark.parametrize(
    ("message", "code"),
    [("   ", "EMPTY_INPUT"), ("a" * 4001, "INPUT_TOO_LONG")],
)
def test_chat_rejects_invalid_message(client, message, code):
    response = client.post("/chat", json={"conversation_id": _conversation_id(), "message": message})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_chat_rejects_invalid_conversation_id(client):
    response = client.post("/chat", json={"conversation_id": "not-a-uuid", "message": "你好"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CONVERSATION_ID"


def test_chat_maps_timeout_to_503(client, fake_llm):
    fake_llm.mode = "timeout"

    response = client.post("/chat", json={"conversation_id": _conversation_id(), "message": "你好"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_TIMEOUT"


def test_chat_maps_rate_limit_to_503(client, fake_llm):
    fake_llm.mode = "rate_limit"

    response = client.post("/chat", json={"conversation_id": _conversation_id(), "message": "你好"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_RATE_LIMITED"


def test_second_turn_sends_history_to_model(client, fake_llm):
    fake_llm.outputs = ["negative"]
    conversation_id = _conversation_id()

    assert (
        client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": "第一句，我怕失败"},
        ).status_code
        == 200
    )
    first_turn_calls = len(fake_llm.requests)

    assert (
        client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": "第二句，我还是很担心"},
        ).status_code
        == 200
    )

    second_turn_calls = fake_llm.requests[first_turn_calls:]
    assert second_turn_calls
    assert any("第一句，我怕失败" in payload for payload in second_turn_calls)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
