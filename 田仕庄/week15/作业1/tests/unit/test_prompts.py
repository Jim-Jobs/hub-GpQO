import pytest

from app.domain.enums import Sentiment
from app.engines.shared.prompts import build_reply_prompt, build_sentiment_prompt


def test_sentiment_prompt_contains_message_and_both_labels():
    prompt = build_sentiment_prompt("今天面试没过")
    assert "今天面试没过" in prompt
    assert "positive" in prompt
    assert "negative" in prompt


def test_reply_prompt_depends_on_sentiment():
    positive = build_reply_prompt(Sentiment.POSITIVE, "同一句话")
    negative = build_reply_prompt(Sentiment.NEGATIVE, "同一句话")
    assert "同一句话" in positive
    assert "同一句话" in negative
    assert positive != negative


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
