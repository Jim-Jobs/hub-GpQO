"""解析产物 → 可索引 chunk，保留页码与图片。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingest.parser import ParsedBlock

DEFAULT_CHUNK_SIZE = 256


@dataclass
class Chunk:
    text: str
    page: int | None = None
    image_path: str | None = None


def _split_segment(text: str, chunk_size: int) -> list[str]:
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    segments: list[str] = []
    for part in parts:
        if len(part) <= chunk_size:
            segments.append(part)
            continue
        lines = [line.strip() for line in part.splitlines() if line.strip()]
        buffer = ""
        for line in lines:
            if buffer and len(buffer) + 1 + len(line) > chunk_size:
                segments.append(buffer)
                buffer = line
            else:
                buffer = f"{buffer}\n{line}" if buffer else line
        if buffer:
            segments.append(buffer)

    result: list[str] = []
    for segment in segments:
        if len(segment) <= chunk_size:
            result.append(segment)
        else:
            result.extend(
                segment[i : i + chunk_size]
                for i in range(0, len(segment), chunk_size)
            )
    return result


def chunk_blocks(
    blocks: list[ParsedBlock], chunk_size: int = DEFAULT_CHUNK_SIZE
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_text = ""
    current_page: int | None = None
    current_image: str | None = None

    def flush() -> None:
        nonlocal current_text, current_page, current_image
        text = current_text.strip()
        if text:
            chunks.append(
                Chunk(text=text, page=current_page, image_path=current_image)
            )
        current_text, current_page, current_image = "", None, None

    for block in blocks:
        piece = block.text.strip()
        if block.image_path:
            image_md = f"![fig](images/{Path(block.image_path).name})"
            piece = f"{piece}\n{image_md}".strip()

        if not piece:
            continue

        for segment in _split_segment(piece, chunk_size):
            if not current_text:
                current_text, current_page, current_image = (
                    segment,
                    block.page,
                    block.image_path,
                )
                continue

            page_changed = (
                block.page is not None
                and current_page is not None
                and block.page != current_page
            )
            if page_changed or len(current_text) + 1 + len(segment) > chunk_size:
                flush()
                current_text, current_page, current_image = (
                    segment,
                    block.page,
                    block.image_path,
                )
            else:
                current_text = f"{current_text}\n{segment}"
                if block.image_path and not current_image:
                    current_image = block.image_path

    flush()
    return chunks
