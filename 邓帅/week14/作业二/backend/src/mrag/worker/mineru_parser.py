"""MinerU 4.x 解析封装：PDF -> markdown + 图片文件。

4.x 为 client-server 文档中心架构，与参考项目（2.x CLI `mineru -p -o -b`）差异大：
1. `mineru parse <pdf> -o <md文件>` 输出 markdown；图片以定位符引用：
   ![Chart block](doc:<sha前8>/tier:standard/page:1/block:2)
2. `mineru read <定位符> -f image -o <png>` 从文档库导出图片二进制

本模块封装一站式流程：parse -> 导出全部图片 -> md 中的定位符重写为
images/<n>.png 相对路径（与 md 同目录），对下游（切块/重写/CLIP）透明。
环境前提：mineru server 已启动（本地 managed 引擎 + standard 档模型已下载）。
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

LOCATOR_RE = re.compile(r"!\[([^\]]*)\]\((doc:[^)/]+/tier:[^)/]+/page:\d+/block:\d+)\)")


class MineruError(RuntimeError):
    pass


def _mineru_bin() -> str:
    """定位 mineru 可执行：优先 python 同环境 bin（不依赖 PATH），其次 which。"""
    candidate = Path(sys.executable).parent / "mineru"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("mineru")
    if found:
        return found
    raise MineruError("mineru 命令不存在（pip install mineru 后重试）")


def _run(args: list[str], timeout: int) -> str:
    cmd = [_mineru_bin(), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    except subprocess.TimeoutExpired as e:
        raise MineruError(f"mineru 超时（>{timeout}s）: {' '.join(cmd[:3])}") from e
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise MineruError(f"mineru 退出码 {proc.returncode}: {(proc.stderr or out)[-500:]}")
    return out


def parse_pdf(pdf_path: str | Path, out_dir: str | Path, wait_sec: int = 600) -> tuple[Path, Path]:
    """一站式解析。返回 (markdown 路径, images 目录)。

    out_dir: 该文档专属目录（如 data/processed/<stem>/），md 与 images/ 都在其中。
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{pdf_path.stem}.md"

    _run(
        ["parse", str(pdf_path), "-o", str(md_path), "--json", "--wait", str(wait_sec)],
        timeout=wait_sec + 60,
    )
    if not md_path.is_file():
        raise MineruError(f"mineru 未产出 markdown: {md_path}")

    md_text = md_path.read_text(encoding="utf-8", errors="replace")
    images_dir = out_dir / "images"
    md_text = export_and_rewrite_images(md_text, images_dir)
    md_path.write_text(md_text, encoding="utf-8")
    return md_path, images_dir


def export_and_rewrite_images(md_text: str, images_dir: Path) -> str:
    """导出全部定位符图片到 images_dir，并把 md 引用重写为 images/<n>.png。"""
    matches = list(LOCATOR_RE.finditer(md_text))
    if not matches:
        return md_text
    images_dir.mkdir(parents=True, exist_ok=True)

    rewritten = md_text
    for i, m in enumerate(reversed(matches), start=1):  # 倒序替换避免偏移失效
        idx = len(matches) - i  # 正序索引
        locator = m.group(2)
        img_path = images_dir / f"img_{idx:03d}.png"
        out = _run(
            ["read", locator, "-f", "image", "-o", str(img_path), "--json"],
            timeout=120,
        )
        if not img_path.is_file():
            raise MineruError(f"图片导出失败（{locator}）: {out[-300:]}")
        rewritten = rewritten.replace(m.group(0), f"![{m.group(1)}](images/{img_path.name})", 1)
    return rewritten


# ---- 兼容入口（worker 调用） ----


def run_mineru(pdf_path: str | Path, out_dir: str | Path, wait_sec: int = 600) -> Path:
    """解析并返回 markdown 路径（旧签名保留，内部走 parse_pdf）。"""
    md_path, _ = parse_pdf(pdf_path, out_dir, wait_sec)
    return md_path


def locate_outputs(out_dir: str | Path, pdf_stem: str) -> tuple[Path, Path]:
    """定位产物（parse_pdf 直接返回；此函数供重试/诊断场景重扫目录）。"""
    out = Path(out_dir)
    mds = sorted(out.rglob("*.md"))
    if not mds:
        raise MineruError(f"产物中未找到 markdown: {out}")
    md_path = mds[0]
    return md_path, md_path.parent / "images"
