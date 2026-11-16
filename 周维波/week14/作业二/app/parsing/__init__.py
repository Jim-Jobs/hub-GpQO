"""解析器工厂：PARSER_BACKEND=mineru|pymupdf 切换。"""

from __future__ import annotations

from ..config import settings
from .base import BaseParser, ImageRef, ParseError, ParseResult
from .mineru_parser import MineruParser
from .pymupdf_parser import PyMuPDFParser

_PARSERS: dict[str, type[BaseParser]] = {
    "mineru": MineruParser,
    "pymupdf": PyMuPDFParser,
    "local": PyMuPDFParser,
    "fallback": PyMuPDFParser,
}


def get_parser(name: str | None = None) -> BaseParser:
    backend = (name or settings.parser_backend).lower()
    parser_cls = _PARSERS.get(backend)
    if parser_cls is None:
        raise ValueError(
            f"未知 PARSER_BACKEND={backend}，可选值：{sorted(_PARSERS)}"
        )
    return parser_cls()


__all__ = [
    "BaseParser",
    "ImageRef",
    "MineruParser",
    "ParseError",
    "ParseResult",
    "PyMuPDFParser",
    "get_parser",
]
