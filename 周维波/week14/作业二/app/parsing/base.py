"""PDF 解析适配层：统一输出 markdown + 图片目录，屏蔽 MinerU / PyMuPDF 差异。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ParseError(RuntimeError):
    """解析失败（可重试或直接失败，由 worker 决定）。"""


@dataclass
class ImageRef:
    rel_path: str  # 相对 parsed_dir 的路径，统一用 / 分隔，如 images/abc.jpg
    page_no: int | None = None


@dataclass
class ParseResult:
    markdown: str
    parsed_dir: Path
    images: list[ImageRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    engine: str = ""


class BaseParser(ABC):
    name: str = "base"

    @abstractmethod
    def parse(self, pdf_path: Path, out_dir: Path) -> ParseResult:
        """解析 PDF，产物写到 out_dir 下，返回 markdown 与图片清单。"""
        raise NotImplementedError
