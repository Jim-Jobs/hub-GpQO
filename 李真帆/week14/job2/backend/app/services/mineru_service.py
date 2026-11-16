import logging
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class ParseError(Exception):
    pass


@dataclass
class ParsedDoc:
    md_text: str
    page_count: int
    description: str


def parse_pdf(doc_id: str, pdf_path: str) -> ParsedDoc:
    """调用 mineru-kit 解析 PDF 并解压产物到 data/parsed/{doc_id}/raw/，返回改写图片链接后的 markdown。"""
    zip_path = settings.tmp_dir / f"{doc_id}.zip"
    unzip_dir = settings.tmp_dir / f"{doc_id}_unzip"
    out_dir = settings.parsed_dir / doc_id
    zip_path.unlink(missing_ok=True)
    shutil.rmtree(unzip_dir, ignore_errors=True)
    shutil.rmtree(out_dir, ignore_errors=True)

    cmd = [
        settings.mineru_bin, "parse", pdf_path,
        "--format", "zip", "-o", str(zip_path), "--tier", settings.mineru_tier,
    ]
    logger.info("mineru cmd: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=settings.mineru_timeout,
        )
    except FileNotFoundError as e:
        raise ParseError(f"找不到 mineru 命令: {settings.mineru_bin}（请在 .env 配置完整路径）") from e
    except subprocess.TimeoutExpired as e:
        raise ParseError(f"mineru 解析超时（>{settings.mineru_timeout}s）") from e
    if proc.returncode != 0 or not zip_path.exists():
        stderr = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise ParseError(f"mineru 解析失败: {stderr or '无输出'}")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(unzip_dir)
    except zipfile.BadZipFile as e:
        raise ParseError(f"mineru 产物不是有效 zip: {e}") from e

    # 防御式适配：不假设固定文件名，取最大的 .md 为主文档，其余全量保留
    md_files = sorted(unzip_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if not md_files:
        raise ParseError("解压产物中未找到 markdown 文件")
    main_md = md_files[0]

    # 保留原始目录结构，便于图片相对路径稳定可追溯
    raw_dir = out_dir / "raw"
    raw_dir.parent.mkdir(parents=True, exist_ok=True)
    main_md_rel = main_md.relative_to(unzip_dir)
    shutil.move(str(unzip_dir), str(raw_dir))
    main_md = raw_dir / main_md_rel

    md_text = main_md.read_text(encoding="utf-8", errors="ignore")
    md_rel_dir = main_md.parent.relative_to(raw_dir)
    md_text = _rewrite_image_links(doc_id, raw_dir, md_rel_dir, md_text)

    page_count = _count_pages(raw_dir)
    description = _make_description(md_text)
    zip_path.unlink(missing_ok=True)
    return ParsedDoc(md_text=md_text, page_count=page_count, description=description)


def _rewrite_image_links(doc_id: str, raw_dir: Path, md_rel_dir: Path, md_text: str) -> str:
    def repl(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2).strip()
        if url.startswith(("http://", "https://", "/static/", "data:")):
            return m.group(0)
        target = (md_rel_dir / url).as_posix()
        if not (raw_dir / target).exists():
            return m.group(0)
        return f"![{alt}](/static/parsed/{doc_id}/raw/{target})"

    return MD_IMG_RE.sub(repl, md_text)


def _count_pages(raw_dir: Path) -> int:
    import json

    best = 0
    # mineru 4.x 新 schema: 顶层 {"pages": [...]}; 旧版为 [{"page_idx": ...}] 列表
    for name in ("structured_content.json", "middle_json.json", "content_list.json"):
        for cj in raw_dir.rglob(name):
            try:
                data = json.loads(cj.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(data, dict):
                    items = data.get("pages")
                    if items is None:
                        items = data.get("pdf_info", [])
                else:
                    items = data
                if isinstance(items, list) and items:
                    idxs = [
                        it.get("page_idx", i)
                        for i, it in enumerate(items)
                        if isinstance(it, dict)
                    ]
                    if idxs:
                        best = max(best, max(idxs) + 1)
            except Exception:
                continue
    return best


def _make_description(md_text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md_text)
    text = re.sub(r"<[^>]+>", " ", text)  # 去掉 mineru 输出的 details/summary 等 HTML 标签
    text = re.sub(r"[#*`>|_\-\[\]()]", "", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return re.sub(r"\s+", " ", " ".join(lines))[:200]
