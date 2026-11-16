"""MinerU 解析：调用 mineru CLI，产出 markdown + images/，并尽力回填页码。"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import settings
from .base import BaseParser, ImageRef, ParseError, ParseResult

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_TEXT_PAGE_PREFIX_LEN = 60


class MineruParser(BaseParser):
    name = "mineru"

    def build_command(self, pdf_path: Path, out_dir: Path) -> list[str]:
        executable = settings.mineru_executable
        if shutil.which(executable):
            cmd = [executable]
        else:
            # pip 安装的 mineru 有时不在 PATH（或 Windows 下没有 .exe 入口）
            cmd = [sys.executable, "-m", "mineru"]

        cmd += [
            "-p",
            str(pdf_path),
            "-o",
            str(out_dir),
            "-b",
            settings.mineru_backend,
        ]
        if settings.mineru_backend == "vlm-http-client" and settings.mineru_api_url:
            cmd += ["-u", settings.mineru_api_url]
        if settings.mineru_extra_args:
            cmd += shlex.split(settings.mineru_extra_args, posix=True)
        return cmd

    def parse(self, pdf_path: Path, out_dir: Path) -> ParseResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(pdf_path, out_dir)
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.mineru_timeout_sec,
            )
        except FileNotFoundError as exc:
            raise ParseError(
                "未找到 mineru 可执行文件，请先安装 MinerU（pip install -U mineru）"
                "或改用 PARSER_BACKEND=pymupdf"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ParseError(
                f"MinerU 解析超时（>{settings.mineru_timeout_sec}s），可调大 MINERU_TIMEOUT_SEC"
            ) from exc
        except OSError as exc:
            raise ParseError(f"MinerU 启动失败：{exc}") from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()[-800:]
            stdout = (completed.stdout or "").strip()[-400:]
            raise ParseError(
                f"MinerU 退出码 {completed.returncode}：{stderr or stdout or '无输出'}"
            )

        markdown_path = self._pick_markdown(out_dir, pdf_path)
        if markdown_path is None:
            raise ParseError("MinerU 执行成功但未找到 markdown 产物")

        markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        parsed_dir = markdown_path.parent
        content_list_path = self._pick_content_list(out_dir)
        meta: dict[str, Any] = {
            "markdown_path": str(markdown_path),
            "content_list_path": str(content_list_path) if content_list_path else None,
            "cmd": cmd,
        }
        if content_list_path is not None:
            meta.update(self._page_index_from_content_list(content_list_path))

        image_pages: dict[str, int] = meta.get("image_pages", {})
        images: list[ImageRef] = []
        for path in sorted(parsed_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                rel = path.relative_to(parsed_dir).as_posix()
                images.append(ImageRef(rel_path=rel, page_no=image_pages.get(path.name)))

        return ParseResult(
            markdown=markdown,
            parsed_dir=parsed_dir,
            images=images,
            meta=meta,
            engine=self.name,
        )

    @staticmethod
    def _pick_markdown(out_dir: Path, pdf_path: Path) -> Path | None:
        candidates = sorted(
            out_dir.rglob("*.md"), key=lambda item: item.stat().st_size, reverse=True
        )
        if not candidates:
            return None
        for candidate in candidates:
            if candidate.stem == pdf_path.stem:
                return candidate
        return candidates[0]

    @staticmethod
    def _pick_content_list(out_dir: Path) -> Path | None:
        candidates = sorted(out_dir.rglob("*_content_list.json"))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stat().st_size)

    @staticmethod
    def _page_index_from_content_list(path: Path) -> dict[str, Any]:
        """从 MinerU 的 content_list.json 抽 page_idx，建立图片/文本 -> 页码映射。"""
        try:
            blocks = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(blocks, list):
            return {}

        image_pages: dict[str, int] = {}
        text_pages: list[list[Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            page_idx = block.get("page_idx")
            page_no = int(page_idx) + 1 if isinstance(page_idx, int) else None
            img_path = block.get("img_path") or ""
            if img_path and page_no is not None:
                image_pages[Path(str(img_path)).name] = page_no

            for key in ("text", "table_body", "content"):
                value = block.get(key)
                if not isinstance(value, str) or not value.strip() or page_no is None:
                    continue
                prefix = " ".join(value.split())[:_TEXT_PAGE_PREFIX_LEN]
                if prefix:
                    text_pages.append([prefix, page_no])
                    break
        return {"image_pages": image_pages, "text_pages": text_pages}
