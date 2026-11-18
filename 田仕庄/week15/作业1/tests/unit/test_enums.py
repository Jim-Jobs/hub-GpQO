import pytest

from app.domain.enums import Sentiment


def test_values():
    assert Sentiment.POSITIVE.value == "positive"
    assert Sentiment.NEGATIVE.value == "negative"


def test_lookup():
    assert Sentiment("negative") is Sentiment.NEGATIVE


def test_rejects_unknown_value():
    with pytest.raises(ValueError):
        Sentiment("neutral")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
