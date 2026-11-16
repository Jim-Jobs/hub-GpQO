"""paths 工具单测：图片路径解析（修复参考 bug 的核心）与 URL 重写。"""

from pathlib import Path

from mrag.paths import (
    abs_to_files_url,
    extract_image_refs,
    image_ref_to_abs,
    rewrite_chunk_images,
)


def _make(md_dir: Path, img_rel: str) -> Path:
    img = md_dir / img_rel
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"x")
    return img


def test_extract_refs():
    text = "![a](images/1.jpg) 文字 ![b](images/2.png)"
    assert extract_image_refs(text) == ["images/1.jpg", "images/2.png"]


def test_image_ref_to_abs_joins_separator(tmp_path: Path):
    """参考项目的 bug 复现：拼接必须带分隔符（其用 dirname+filename 直连导致路径必错）。"""
    md = tmp_path / "doc.md"
    _make(tmp_path, "images/fig1.jpg")
    got = image_ref_to_abs(md, "images/fig1.jpg")
    assert got == (tmp_path / "images/fig1.jpg").resolve()
    assert str(got).endswith("/images/fig1.jpg")  # 分隔符存在
    assert got.is_file()


def test_image_ref_to_abs_url_passthrough(tmp_path):
    md = tmp_path / "doc.md"
    # 外链不解析为本地文件（Path 规范化会压掉双斜杠，行为契约是不命中本地文件）
    assert image_ref_to_abs(md, "https://x/1.jpg").is_file() is False


def test_abs_to_files_url(tmp_path):
    root = tmp_path / "processed"  # processed_root；挂载根是 tmp_path
    p = _make(root / "报告", "images/a.jpg")
    assert abs_to_files_url(p, tmp_path) == "/files/processed/报告/images/a.jpg"
    # 挂载根之外（tmp_path 兄弟路径）返回 None；根下任意路径都合法
    assert abs_to_files_url(tmp_path.parent / "elsewhere.jpg", tmp_path) is None
    assert abs_to_files_url(tmp_path / "top.jpg", tmp_path) == "/files/top.jpg"


def test_rewrite_chunk_images(tmp_path):
    _make(tmp_path, "images/fig.jpg")
    chunk = "前文\n![图](images/fig.jpg)\n后文 ![缺](images/missing.jpg)"
    out, paths = rewrite_chunk_images(chunk, tmp_path / "doc.md", tmp_path)
    assert "/files/" in out and "images/fig.jpg)" in out  # URL 相对挂载根(tmp_path)
    assert len(paths) == 1 and paths[0].endswith("fig.jpg")
    assert "images/missing.jpg" in out  # 缺失文件保持原样


def test_rewrite_no_images(tmp_path):
    out, paths = rewrite_chunk_images("纯文本", tmp_path / "d.md", tmp_path)
    assert out == "纯文本" and paths == []
