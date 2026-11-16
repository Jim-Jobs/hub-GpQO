"""PyMuPDF 兜底解析：不依赖 GPU / MinerU，用于本地开发与 CI 跑通全链路。

产物结构故意与 MinerU 对齐：markdown（带 `<!-- page: N -->` 页码标记）+ images/ 目录。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # PyMuPDF 2.x 的正式包名，fitz 为兼容别名

from .base import BaseParser, ImageRef, ParseError, ParseResult

MIN_IMAGE_WIDTH = 80
MIN_IMAGE_HEIGHT = 60


class PyMuPDFParser(BaseParser):
    name = "pymupdf"

    def parse(self, pdf_path: Path, out_dir: Path) -> ParseResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        try:
            document = fitz.open(str(pdf_path))
        except Exception as exc:  # PyMuPDF 抛出的异常类型不统一
            raise ParseError(f"无法打开 PDF：{exc}") from exc

        parts: list[str] = []
        images: list[ImageRef] = []
        seen_xrefs: set[int] = set()

        try:
            for page_index, page in enumerate(document, start=1):
                parts.append(f"<!-- page: {page_index} -->")
                text = page.get_text("text").strip()
                if text:
                    parts.append(text)

                for image_index, image in enumerate(page.get_images(full=True)):
                    xref = int(image[0])
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        pixmap = fitz.Pixmap(document, xref)
                        if pixmap.width < MIN_IMAGE_WIDTH or pixmap.height < MIN_IMAGE_HEIGHT:
                            continue
                        if pixmap.n - pixmap.alpha >= 4:
                            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                    except Exception:
                        continue

                    name = f"page{page_index}_img{image_index}.png"
                    target = images_dir / name
                    try:
                        pixmap.save(str(target))
                    except Exception:
                        continue
                    rel = f"images/{name}"
                    images.append(ImageRef(rel_path=rel, page_no=page_index))
                    parts.append(f"![{name}]({rel})")
        finally:
            document.close()

        markdown = "\n\n".join(parts)
        if not markdown.strip():
            raise ParseError("PDF 中没有可提取的文本或图片（可能是加密文档或空文件）")

        return ParseResult(
            markdown=markdown,
            parsed_dir=out_dir,
            images=images,
            meta={"engine": "pymupdf", "page_count": len(parts)},
            engine=self.name,
        )
