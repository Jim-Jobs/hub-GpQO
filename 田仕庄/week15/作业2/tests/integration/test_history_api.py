from uuid import uuid4

import pytest


def _send(client, fake_llm, conversation_id: str, message: str, sentiment: str = "negative") -> None:
    fake_llm.outputs = [sentiment]
    response = client.post(
        "/chat", json={"conversation_id": conversation_id, "message": message}
    )
    assert response.status_code == 200


def test_history_returns_messages_in_order(client, fake_llm):
    conversation_id = str(uuid4())
    _send(client, fake_llm, conversation_id, "第一句")

    response = client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    messages = body["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["sentiment"] is None
    assert messages[1]["sentiment"] == "negative"
    assert all(message["id"] for message in messages)
    assert all(message["created_at"].endswith("Z") for message in messages)


def test_history_unknown_conversation_returns_404(client):
    response = client.get(f"/conversations/{uuid4()}/messages")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_history_before_cursor_returns_earlier_messages(client, fake_llm):
    conversation_id = str(uuid4())
    _send(client, fake_llm, conversation_id, "第一句")
    _send(client, fake_llm, conversation_id, "第二句")

    messages = client.get(f"/conversations/{conversation_id}/messages").json()["messages"]
    assert len(messages) == 4

    cursor = messages[2]["id"]
    response = client.get(
        f"/conversations/{conversation_id}/messages", params={"before": cursor}
    )

    assert response.status_code == 200
    earlier = response.json()["messages"]
    assert [message["id"] for message in earlier] == [messages[0]["id"], messages[1]["id"]]


def test_history_applies_limit(client, fake_llm):
    conversation_id = str(uuid4())
    _send(client, fake_llm, conversation_id, "第一句")
    _send(client, fake_llm, conversation_id, "第二句")

    response = client.get(
        f"/conversations/{conversation_id}/messages", params={"limit": 1}
    )

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
