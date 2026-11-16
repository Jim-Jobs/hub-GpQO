"""PDF 解析：MinerU 优先，失败时自动降级到 PyMuPDF。

MinerU 输出 Markdown + 图片（课程文档 02_MinerU.md：mineru -p <input> -o <output>）。
本模块把解析结果统一成：
    data/docs/<doc_id>/content.md     Markdown 正文
    data/docs/<doc_id>/assets/*       文档图片
    data/docs/<doc_id>/meta.json      解析元信息
并把 Markdown 里的图片链接改写成 /media/docs/<doc_id>/assets/xxx，供前端图文混排展示。
"""

from __future__ import annotations

import io
import json
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

from .config import settings

logger = logging.getLogger("mmrag.parser")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_IMG_RE = re.compile(r"(<img[^>]*?src\s*=\s*[\"'])([^\"']+)([\"'])", re.IGNORECASE)
PAGE_HEAD_RE = re.compile(r"^#{1,4}\s*第\s*(\d+)\s*页", re.MULTILINE)


def _pdf_page_count(pdf_path: Path) -> int:
    try:
        import fitz

        with fitz.open(str(pdf_path)) as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001
        pass
    try:
        import pypdf

        return len(pypdf.PdfReader(str(pdf_path)).pages)
    except Exception:  # noqa: BLE001
        return 0


def _collect_images(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and not any(word in p.name.lower() for word in ("layout", "span", "thumb"))
    ]


def _pick_markdown(root: Path) -> tuple[str, list[Path]]:
    md_files = [p for p in root.rglob("*.md") if p.is_file()]
    if not md_files:
        raise RuntimeError("未找到 .md 解析结果")
    md_path = max(md_files, key=lambda p: p.stat().st_size)
    return md_path.read_text(encoding="utf-8", errors="replace"), _collect_images(root)


def _run_mineru_cli(pdf_path: Path, tmp_dir: Path) -> tuple[str, list[Path], int]:
    exe = shutil.which(settings.mineru_bin)
    if not exe:
        raise RuntimeError(f"未找到 MinerU 命令 {settings.mineru_bin!r}，请先安装 mineru[core]")
    out_dir = tmp_dir / "mineru_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", str(pdf_path), "-o", str(out_dir)] + shlex.split(settings.mineru_extra_args)
    logger.info("执行 MinerU：%s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.mineru_timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"MinerU 退出码 {proc.returncode}：{(proc.stderr or proc.stdout)[-600:]}")
    markdown, images = _pick_markdown(out_dir)
    if not markdown.strip():
        raise RuntimeError("MinerU 生成的 Markdown 为空")
    return markdown, images, _pdf_page_count(pdf_path)


def _extract_markdown_from_json(data, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n\n".join(part for item in data[:50] if (part := _extract_markdown_from_json(item, depth + 1)))
    if isinstance(data, dict):
        for key in ("markdown", "md", "content", "text", "result", "output"):
            value = data.get(key)
            if isinstance(value, str) and len(value) > 20:
                return value
        for value in data.values():
            part = _extract_markdown_from_json(value, depth + 1)
            if part and len(part) > 20:
                return part
    return ""


def _extract_images_from_json(data, tmp_dir: Path) -> list[Path]:
    """兼容部分 MinerU HTTP 服务返回 base64 图片列表的情况。"""
    import base64

    images: list[Path] = []
    seen: set[int] = set()

    def add(item) -> None:
        raw = None
        ext = ".png"
        if isinstance(item, str) and item.startswith("data:"):
            raw = item.split(",", 1)[-1]
        elif isinstance(item, dict):
            raw = item.get("base64") or item.get("data") or item.get("content")
            suffix = Path(str(item.get("name", ""))).suffix.lower()
            ext = suffix if suffix in IMAGE_SUFFIXES else ".png"
        if not raw:
            return
        try:
            path = tmp_dir / f"api_image_{len(images) + 1:03d}{ext}"
            path.write_bytes(base64.b64decode(raw))
            images.append(path)
        except Exception:  # noqa: BLE001
            pass

    def walk(node, depth: int) -> None:
        if depth > 5 or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, dict):
            for key in ("images", "assets"):
                value = node.get(key)
                if isinstance(value, list):
                    seen.add(id(value))
                    for item in value[:40]:
                        add(item)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node[:40]:
                if isinstance(item, (dict, list)):
                    walk(item, depth + 1)
                else:
                    add(item)

    walk(data, 0)
    return images


def _run_mineru_api(pdf_path: Path, tmp_dir: Path) -> tuple[str, list[Path], int]:
    import requests

    if not settings.mineru_api_url:
        raise RuntimeError("未配置 MINERU_API_URL")
    with pdf_path.open("rb") as handle:
        response = requests.post(
            settings.mineru_api_url,
            files={"file": (pdf_path.name, handle, "application/pdf")},
            timeout=settings.mineru_timeout,
        )
    response.raise_for_status()
    content = response.content
    content_type = response.headers.get("content-type", "").lower()
    if "zip" in content_type or content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            archive.extractall(tmp_dir / "api_out")
        markdown, images = _pick_markdown(tmp_dir / "api_out")
        return markdown, images, _pdf_page_count(pdf_path)
    if "json" in content_type or content.lstrip().startswith(b"{"):
        data = response.json()
        markdown = _extract_markdown_from_json(data)
        if not markdown.strip():
            raise RuntimeError("MinerU API 响应中没有 Markdown 字段")
        return markdown, _extract_images_from_json(data, tmp_dir), _pdf_page_count(pdf_path)
    markdown = content.decode("utf-8", errors="replace")
    return markdown, [], _pdf_page_count(pdf_path)


def _run_pymupdf(pdf_path: Path, tmp_dir: Path) -> tuple[str, list[Path], int]:
    """降级方案：逐页抽取文本 + 渲染整页图片，依然能支持多模态问答。"""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("未安装 PyMuPDF，请执行：pip install PyMuPDF") from exc

    image_dir = tmp_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for index, page in enumerate(doc):
            text = page.get_text("text").strip()
            name = f"page_{index + 1:03d}.png"
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            pixmap.save(str(image_dir / name))
            parts.append(
                f"## 第 {index + 1} 页\n\n{text or '（本页无可抽取文本，请结合页面图片理解）'}"
                f"\n\n![第 {index + 1} 页页面图](images/{name})\n"
            )
        pages = int(doc.page_count)
    return "\n".join(parts), _collect_images(tmp_dir), pages


def _rewrite_images(markdown: str, mapping: dict[str, str], assets: list[str]) -> str:
    """把 MinerU 输出的相对图片路径替换为前端可访问的 /media URL。"""
    found = [0]

    def lookup(ref: str) -> str | None:
        clean = unquote(ref.split("?")[0].split("#")[0]).strip().replace("\\", "/").lower()
        return mapping.get("path:" + clean) or mapping.get("name:" + Path(clean).name)

    def md_repl(match: re.Match) -> str:
        url = lookup(match.group(2))
        if url:
            found[0] += 1
            return f"![{match.group(1)}]({url})"
        return match.group(0)

    def html_repl(match: re.Match) -> str:
        url = lookup(match.group(2))
        if url:
            found[0] += 1
            return f"{match.group(1)}{url}{match.group(3)}"
        return match.group(0)

    markdown = MD_IMAGE_RE.sub(md_repl, markdown)
    markdown = HTML_IMG_RE.sub(html_repl, markdown)
    if found[0] == 0 and assets:
        # 有的解析器只产出图片目录、Markdown 里没有引用，这里补一个图片区保证多模态可用
        markdown += "\n\n## 文档图片\n\n" + "\n\n".join(
            f"![文档图片 {i + 1}]({url})" for i, url in enumerate(assets[:20])
        )
    return markdown


def parse_pdf(pdf_path: Path, doc_id: str, doc_dir: Path) -> dict:
    """解析单个 PDF 并落盘，返回解析结果摘要。"""
    pdf_path = Path(pdf_path)
    doc_dir = Path(doc_dir)
    assets_dir = doc_dir / "assets"
    doc_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    for old in assets_dir.glob("*"):
        if old.is_file():
            old.unlink()

    tmp_root = settings.data_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="parse_", dir=str(tmp_root)))

    backend_order = {
        "auto": ["mineru", "mineru-api", "pymupdf"],
        "mineru": ["mineru", "pymupdf"],
        "mineru-api": ["mineru-api", "pymupdf"],
        "pymupdf": ["pymupdf"],
    }.get(settings.parser_backend, ["mineru", "mineru-api", "pymupdf"])

    attempts: list[str] = []
    try:
        for backend in backend_order:
            if backend == "mineru-api" and not settings.mineru_api_url:
                continue
            try:
                if backend == "mineru":
                    markdown, image_paths, pages = _run_mineru_cli(pdf_path, tmp_dir)
                elif backend == "mineru-api":
                    markdown, image_paths, pages = _run_mineru_api(pdf_path, tmp_dir)
                else:
                    markdown, image_paths, pages = _run_pymupdf(pdf_path, tmp_dir)
            except Exception as exc:  # noqa: BLE001 - 记录后尝试下一个解析器
                logger.warning("[%s] 解析器 %s 失败：%s", doc_id, backend, exc)
                attempts.append(f"{backend}: {exc}")
                continue

            mapping: dict[str, str] = {}
            assets: list[str] = []
            used_names: set[str] = set()
            for source in image_paths:
                name = source.name
                stem, suffix = Path(name).stem, Path(name).suffix
                counter = 1
                while name.lower() in used_names:
                    name = f"{stem}_{counter}{suffix}"
                    counter += 1
                used_names.add(name.lower())
                shutil.copy2(source, assets_dir / name)
                url = settings.media_url(f"docs/{doc_id}/assets/{name}")
                assets.append(url)
                mapping[f"name:{source.name.lower()}"] = url
                try:
                    rel = str(source.relative_to(tmp_dir)).replace("\\", "/").lower()
                    mapping[f"path:{rel}"] = url
                except ValueError:
                    pass
            markdown = _rewrite_images(markdown, mapping, assets)
            if not pages:
                numbers = [int(n) for n in PAGE_HEAD_RE.findall(markdown)]
                pages = max(numbers) if numbers else len(assets)
            (doc_dir / "content.md").write_text(markdown, encoding="utf-8")
            meta = {
                "doc_id": doc_id,
                "backend": backend,
                "pages": pages,
                "assets": assets,
                "fallback_log": attempts,
            }
            (doc_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[%s] 解析完成 backend=%s pages=%s images=%s", doc_id, backend, pages, len(assets))
            return {"markdown": markdown, "assets": assets, "pages": pages, "backend": backend, "attempts": attempts}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    raise RuntimeError("所有解析器均失败：" + " | ".join(attempts))
