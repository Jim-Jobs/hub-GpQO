"""HTML 表格 → Markdown 表格。

对应文档：``docs/02-数据模型.md`` §3.1「表格 → Markdown 表格」。

**为什么不用 HTML**：HTML 噪声大（``<span style=...>``、实体），既占 token 又干扰 BM25
分词。Markdown 表格既紧凑又是合法文本，双重收益。

实测确认（2026-11-12，MinerU v4 精准解析 + ``model_version=vlm``）：
``content_list`` 的 ``table_body`` 就是 ``<table>...</table>`` 的 HTML 字符串。

``rowspan`` / ``colspan`` 用空单元格占位补全，保证每行列数一致（否则 Markdown 表格会散架）。
解析失败时退化为去标签取纯文本 —— 宁可丢结构，也不丢内容。
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """压缩空白并把 Markdown 表格的分隔符 ``|`` 转义，避免破坏列结构。"""
    text = _WS_RE.sub(" ", text).strip()
    return text.replace("|", "\\|")


class _TableParser(HTMLParser):
    """把 ``<table>`` 解析成二维单元格文本（已处理 rowspan / colspan）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._cell_chunks: list[str] | None = None
        self._cell_span = 1
        self._row_span = 1
        # 尚未填充的跨行占位：(行偏移, 列索引)
        self._pending: list[tuple[int, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._cell_chunks = []
            self._cell_span = _span(attrs_map.get("colspan"))
            self._row_span = _span(attrs_map.get("rowspan"))

    def handle_data(self, data: str) -> None:
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell_chunks is not None:
            cell = _clean("".join(self._cell_chunks))
            if self._current_row is None:
                self._current_row = []
            self._current_row.append(cell)
            for _ in range(self._cell_span - 1):
                self._current_row.append("")
            if self._row_span > 1 and len(self.rows) > 0:
                for offset in range(1, self._row_span):
                    self._pending.append((len(self.rows) + offset, len(self._current_row) - 1))
            self._cell_chunks = None
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None

    def padded_rows(self) -> list[list[str]]:
        """补全所有行到相同列数（含跨行单元格的占位）。"""
        rows = [list(row) for row in self.rows]
        for row_index, col_index in self._pending:
            while len(rows) <= row_index:
                rows.append([])
            row = rows[row_index]
            while len(row) <= col_index:
                row.append("")
        width = max((len(row) for row in rows), default=0)
        for row in rows:
            row.extend([""] * (width - len(row)))
        return [row for row in rows if any(cell for cell in row)]


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def html_to_markdown_table(html: str) -> str:
    """HTML 表格 → Markdown 表格。解析失败返回去标签的纯文本。"""
    if not html or "<table" not in html.lower():
        return _strip_tags(html or "")

    parser = _TableParser()
    try:
        parser.feed(html)
        parser.close()
        rows = parser.padded_rows()
    except Exception:  # noqa: BLE001 —— 解析失败必须降级而不是丢内容
        logger.warning("HTML 表格解析失败，降级为纯文本", exc_info=True)
        return _strip_tags(html)

    if not rows:
        return _strip_tags(html)

    header = rows[0]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _strip_tags(html: str) -> str:
    """去标签取纯文本，尽量保留原有的空白结构。"""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*(td|th)\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*tr\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()
