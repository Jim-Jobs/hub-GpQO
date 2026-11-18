import pytest

from app.domain.enums import Sentiment
from app.engines.shared.parser import parse_sentiment


@pytest.mark.parametrize("raw", ["positive", " Positive ", "POSITIVE.", "positive\n"])
def test_parses_positive(raw):
    assert parse_sentiment(raw) is Sentiment.POSITIVE


@pytest.mark.parametrize("raw", ["negative", "Negative", "NEGATIVE!", "unknown", "", "中立"])
def test_falls_back_to_negative(raw):
    assert parse_sentiment(raw) is Sentiment.NEGATIVE


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
