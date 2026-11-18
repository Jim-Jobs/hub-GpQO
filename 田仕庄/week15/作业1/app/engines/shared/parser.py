import hashlib
import logging

from app.domain.enums import Sentiment

logger = logging.getLogger("app.parser")


def parse_sentiment(raw: str) -> Sentiment:
    """只认 positive；其余输出（含空串、异常文本）一律回退 negative。"""
    normalized = raw.strip().lower().strip(" \t\r\n.,!?:;\"'。！？，；：")
    if normalized == "positive":
        return Sentiment.POSITIVE
    if normalized == "negative":
        return Sentiment.NEGATIVE
    logger.warning(
        "parse_failed",
        extra={
            "raw_length": len(raw),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8],
        },
    )
    return Sentiment.NEGATIVE


if __name__ == "__main__":
    for text in ("positive", " Positive. ", "negative", "unknown"):
        print(repr(text), "->", parse_sentiment(text).value)
