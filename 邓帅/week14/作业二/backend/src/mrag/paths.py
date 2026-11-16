"""markdown 图片路径工具。

修复参考项目的关键 bug：参考代码用 os.path.dirname(md_path) + filename
直接字符串拼接（缺路径分隔符），导致图片路径必然错误 -> CLIP 图像向量全为
零向量、图像检索失效。本模块统一用 os.path.join / Path 正确解析。
"""

import re
from pathlib import Path

MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def extract_image_refs(chunk_text: str) -> list[str]:
    """提取 chunk 内 markdown 图片引用的原始相对路径（如 images/fig1.jpg）。"""
    return [m.group(2) for m in MD_IMAGE_RE.finditer(chunk_text)]


def image_ref_to_abs(markdown_path: str | Path, ref: str) -> Path:
    """图片相对引用 -> 本地绝对路径。

    mineru 输出的 md 中图片写法为 images/xxx.jpg（相对 md 所在目录）。
    """
    ref_clean = ref.strip()
    if ref_clean.startswith(("http://", "https://", "/")):
        # 外链或绝对地址：非本地解析产物，直接返回原样（调用方自行处理）
        return Path(ref_clean)
    return (Path(markdown_path).parent / ref_clean).resolve()


def abs_to_files_url(abs_path: str | Path, mounted_root: str | Path) -> str | None:
    """本地绝对路径 -> /files/... URL。

    mounted_root 是 StaticFiles 的挂载根（data/ 目录）；URL = /files/<相对挂载根路径>。
    不在挂载根下返回 None。
    """
    try:
        rel = Path(abs_path).resolve().relative_to(Path(mounted_root).resolve())
    except ValueError:
        return None
    return "/files/" + rel.as_posix()


def rewrite_chunk_images(
    chunk_text: str, markdown_path: str | Path, processed_root: str | Path
) -> tuple[str, list[str]]:
    """把 chunk 中可定位到本地的图片引用重写为 /files/... URL。

    processed_root（data/processed）用于确认图片属于解析产物；URL 相对挂载根
    （processed_root.parent，即 data/）计算，因此形如 /files/processed/<doc>/images/x.jpg。
    返回 (重写后的文本, 本地图片绝对路径列表)。找不到本地文件的引用保持原样。
    """
    mount_root = Path(processed_root).resolve().parent
    resolved: list[str] = []
    rewritten = chunk_text

    def _sub(m: re.Match) -> str:
        ref = m.group(2)
        abs_p = image_ref_to_abs(markdown_path, ref)
        if abs_p.is_file():
            url = abs_to_files_url(abs_p, mount_root)
            if url:
                resolved.append(str(abs_p))
                return f"![{m.group(1)}]({url})"
        return m.group(0)

    rewritten = MD_IMAGE_RE.sub(_sub, rewritten)
    return rewritten, resolved
