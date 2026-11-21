"""两套实现共用的契约：schemas / prompts / config。"""

from shared.schemas import ChatRequest, ChatResponse, SentimentLabel, SentimentResult

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "SentimentLabel",
    "SentimentResult",
]
