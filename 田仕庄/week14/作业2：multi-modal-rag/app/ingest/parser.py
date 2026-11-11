"""PDF 解析：mock 与真实 MinerU 两种实现。

真实模式调用 MinerU CLI，优先读取 content_list.json（含 page_idx），
没有时回退到 Markdown 并按页标记推断。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import (
    ERROR_TEXT,
    MINERU_FAILED,
    MINERU_NOT_INSTALLED,
    MINERU_TIMEOUT,
    PARSE_OUTPUT_MISSING,
)

logger = logging.getLogger(__name__)

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PAGE_MARK = re.compile(
    r"<!--\s*page\s*(\d+)(?:\s+of\s+\d+)?\s*-->", re.IGNORECASE
)
_IMAGE_MD = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
_DOC_IMAGE = re.compile(r"!\[[^\]]*]\((doc:[^)]+)\)")
_MAX_DOC_IMAGES = 30


@dataclass
class ParsedBlock:
    text: str = ""
    page: int | None = None
    image_path: str | None = None


@dataclass
class ParsedDoc:
    blocks: list[ParsedBlock]
    page_count: int | None = None
    markdown_path: str | None = None


class ParseError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or ERROR_TEXT.get(code, "解析失败")
        super().__init__(self.message)


def parse_document(
    pdf_path: str,
    output_dir: Path,
    settings: Settings | None = None,
) -> ParsedDoc:
    settings = settings or get_settings()
    if settings.parse_backend == "mock":
        return _mock_parse(pdf_path, output_dir)
    return _mineru_parse(pdf_path, output_dir, settings)


# ---------------------------------------------------------------------------
# MinerU CLI
# ---------------------------------------------------------------------------
def _mineru_parse(pdf_path: str, output_dir: Path, settings: Settings) -> ParsedDoc:
    if settings.mineru_cli_mode.lower() == "v3":
        return _mineru_v3_parse(pdf_path, output_dir, settings)
    return _mineru_v4_parse(pdf_path, output_dir, settings)


def _mineru_executable(settings: Settings) -> str:
    exe = settings.mineru_cli_path or shutil.which("mineru")
    if not exe:
        raise ParseError(
            MINERU_NOT_INSTALLED,
            "未找到 mineru 命令；可安装 MinerU 或切到 mock/pipeline",
        )
    return exe


def _mineru_v4_parse(
    pdf_path: str, output_dir: Path, settings: Settings
) -> ParsedDoc:
    """MinerU 4.x：parse 子命令输出 Markdown，图片用 read 按 doc 定位符取回。"""
    exe = _mineru_executable(settings)
    out_dir = Path(output_dir) / Path(pdf_path).stem / "v4"
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = out_dir / "document.md"
    wait_sec = max(1, min(int(settings.parse_timeout_sec), 3600))
    tier = settings.mineru_tier
    if tier not in {"flash", "basic", "standard", "advanced"}:
        tier = "flash"

    cmd = [
        exe,
        "parse",
        str(pdf_path),
        "--output",
        str(markdown_path),
        "--format",
        "markdown",
        "--pages",
        "all",
        "--tier",
        tier,
        "--wait",
        str(wait_sec),
        "--force",
    ]
    logger.info("mineru v4 start: %s", " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.parse_timeout_sec + 60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseError(
            MINERU_TIMEOUT,
            f"MinerU 超过 {settings.parse_timeout_sec}s 未完成",
        ) from exc
    except OSError as exc:
        raise ParseError(MINERU_FAILED, f"启动 MinerU 失败: {exc}") from exc

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise ParseError(
            MINERU_FAILED,
            f"MinerU 退出码 {completed.returncode}: {tail}",
        )
    if not markdown_path.is_file():
        raise ParseError(
            PARSE_OUTPUT_MISSING,
            f"未在 {out_dir} 找到 MinerU 4 输出 {markdown_path.name}",
        )

    _extract_doc_images(markdown_path, out_dir, exe, settings)
    return _load_output(out_dir)


def _extract_doc_images(
    markdown_path: Path, out_dir: Path, exe: str, settings: Settings
) -> None:
    text = markdown_path.read_text(encoding="utf-8")
    locators: list[str] = []
    for match in _DOC_IMAGE.finditer(text):
        locator = match.group(1)
        if locator not in locators:
            locators.append(locator)
    if not locators:
        return

    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for index, locator in enumerate(locators[:_MAX_DOC_IMAGES], start=1):
        suffix = ".png"
        destination = images_dir / f"mineru_{index}{suffix}"
        cmd = [
            exe,
            "read",
            locator,
            "--format",
            "image",
            "--output",
            str(destination),
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("mineru read image failed: %s (%s)", locator, exc)
            continue
        if completed.returncode != 0 or not destination.is_file():
            tail = (completed.stderr or completed.stdout or "").strip()[-300:]
            logger.warning(
                "mineru read image skipped: %s rc=%s %s",
                locator,
                completed.returncode,
                tail,
            )
            continue
        relative = f"images/{destination.name}"
        text = text.replace(f"({locator})", f"({relative})")

    markdown_path.write_text(text, encoding="utf-8")


def _mineru_v3_parse(
    pdf_path: str, output_dir: Path, settings: Settings
) -> ParsedDoc:
    exe = _mineru_executable(settings)

    out_dir = Path(output_dir) / Path(pdf_path).stem / "mineru"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", str(pdf_path), "-o", str(out_dir), "-b", settings.mineru_backend]
    if settings.mineru_backend.startswith("vlm"):
        cmd += ["-u", settings.mineru_backend_url]

    logger.info("mineru start: %s", " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.parse_timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseError(
            MINERU_TIMEOUT,
            f"MinerU 超过 {settings.parse_timeout_sec}s 未完成",
        ) from exc
    except OSError as exc:
        raise ParseError(MINERU_FAILED, f"启动 MinerU 失败: {exc}") from exc

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise ParseError(
            MINERU_FAILED,
            f"MinerU 退出码 {completed.returncode}: {tail}",
        )

    return _load_output(out_dir)


def _load_output(out_dir: Path) -> ParsedDoc:
    candidates = sorted(
        Path(out_dir).rglob("*content_list.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return _parse_content_list(candidates[0])

    markdowns = sorted(Path(out_dir).rglob("*.md"), key=lambda path: str(path))
    if not markdowns:
        raise ParseError(
            PARSE_OUTPUT_MISSING,
            f"未在 {out_dir} 找到 content_list.json 或 markdown",
        )
    return _parse_markdown(markdowns)


def _caption_text(value) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _parse_content_list(path: Path) -> ParsedDoc:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParseError(
            PARSE_OUTPUT_MISSING, f"content_list 读取失败: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ParseError(PARSE_OUTPUT_MISSING, "content_list 结构不是数组")

    blocks: list[ParsedBlock] = []
    max_page = 0
    for item in data:
        if not isinstance(item, dict):
            continue

        page_index = item.get("page_idx")
        page = page_index + 1 if isinstance(page_index, int) else None
        if page:
            max_page = max(max_page, page)

        kind = item.get("type")
        if kind == "text":
            text = (item.get("text") or "").strip()
            if text:
                blocks.append(ParsedBlock(text=text, page=page))
        elif kind == "table":
            text = (item.get("table_body") or item.get("text") or "").strip()
            caption = _caption_text(item.get("table_caption"))
            text = "\n".join(part for part in (caption, text) if part)
            if text:
                blocks.append(ParsedBlock(text=text, page=page))
        elif kind == "image":
            image_path = None
            img_path = item.get("img_path")
            if img_path:
                candidate = (path.parent / img_path).resolve()
                if candidate.is_file():
                    image_path = str(candidate)
            caption = _caption_text(
                item.get("image_caption") or item.get("text")
            )
            if image_path or caption:
                blocks.append(
                    ParsedBlock(text=caption, page=page, image_path=image_path)
                )

    if not blocks:
        raise ParseError(PARSE_OUTPUT_MISSING, "content_list 中没有可索引内容")

    return ParsedDoc(blocks=blocks, page_count=max_page or None)


def _parse_markdown(paths: list[Path]) -> ParsedDoc:
    blocks: list[ParsedBlock] = []
    max_page = 0
    current_page: int | None = None

    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            mark = _PAGE_MARK.search(stripped)
            if mark:
                current_page = int(mark.group(1))
                max_page = max(max_page, current_page)
                continue
            image = _IMAGE_MD.search(stripped)
            if image and not _IMAGE_MD.sub("", stripped).strip():
                candidate = (path.parent / image.group(1)).resolve()
                if candidate.is_file():
                    blocks.append(
                        ParsedBlock(
                            text="", page=current_page, image_path=str(candidate)
                        )
                    )
                    continue
            blocks.append(ParsedBlock(text=stripped, page=current_page))

    if not blocks:
        raise ParseError(PARSE_OUTPUT_MISSING, "markdown 内容为空")

    return ParsedDoc(
        blocks=blocks,
        page_count=max_page or None,
        markdown_path=str(paths[0]),
    )


# ---------------------------------------------------------------------------
# mock：不依赖 MinerU，生成可索引的假产物
# ---------------------------------------------------------------------------
def _mock_parse(pdf_path: str, output_dir: Path) -> ParsedDoc:
    stem = Path(pdf_path).stem
    doc_dir = Path(output_dir) / stem / "auto"
    images_dir = doc_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    figure = images_dir / "fig1.png"
    figure.write_bytes(_PNG_1PX)

    items = [
        {"type": "text", "page_idx": 0, "text": "第1页：项目背景与目标说明。"},
        {
            "type": "text",
            "page_idx": 0,
            "text": "本系统通过 Kafka 异步解析 PDF，并支持多模态问答。",
        },
        {
            "type": "image",
            "page_idx": 1,
            "img_path": "images/fig1.png",
            "image_caption": ["图1：系统架构示意"],
        },
        {"type": "text", "page_idx": 2, "text": "第3页：结论与后续计划。"},
    ]
    (doc_dir / "content_list.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    markdown = doc_dir / "mock.md"
    markdown.write_text(
        "\n\n".join(
            [
                "第1页：项目背景与目标说明。",
                "本系统通过 Kafka 异步解析 PDF，并支持多模态问答。",
                "![fig1](images/fig1.png)",
                "第3页：结论与后续计划。",
            ]
        ),
        encoding="utf-8",
    )

    blocks = [
        ParsedBlock(text="第1页：项目背景与目标说明。", page=1),
        ParsedBlock(
            text="本系统通过 Kafka 异步解析 PDF，并支持多模态问答。", page=1
        ),
        ParsedBlock(text="图1：系统架构示意", page=2, image_path=str(figure)),
        ParsedBlock(text="第3页：结论与后续计划。", page=3),
    ]
    logger.debug("mock parsed: %s pages=3", pdf_path)
    return ParsedDoc(blocks=blocks, page_count=3, markdown_path=str(markdown))
