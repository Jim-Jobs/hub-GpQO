"""RAG 能力导出。"""

from __future__ import annotations

from .answerer import BaseAnswerer, MockAnswerer, OpenAIAnswerer, get_answerer
from .assets import absolutize_images, image_url, resolve_asset
from .prompts import SYSTEM_PROMPT, build_user_text
from .retriever import RetrievalResult, retrieve

__all__ = [
    "BaseAnswerer",
    "MockAnswerer",
    "OpenAIAnswerer",
    "RetrievalResult",
    "SYSTEM_PROMPT",
    "absolutize_images",
    "build_user_text",
    "get_answerer",
    "image_url",
    "resolve_asset",
    "retrieve",
]
