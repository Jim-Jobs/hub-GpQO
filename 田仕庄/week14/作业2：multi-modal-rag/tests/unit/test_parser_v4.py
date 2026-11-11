from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.ingest.parser import _extract_doc_images, _parse_markdown


def test_v4_page_markers_and_local_image(isolated_data_dir):
    from app.config import get_settings

    root = Path(get_settings().processed_dir) / "v4"
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    markdown = root / "document.md"
    markdown.write_text(
        "<!-- page 1 of 2 -->\n\n"
        "第一页文字\n\n"
        "![Image block](images/fig.png)\n\n"
        "<!-- page 2 of 2 -->\n\n"
        "第二页文字\n",
        encoding="utf-8",
    )

    parsed = _parse_markdown([markdown])

    assert parsed.page_count == 2
    assert [block.page for block in parsed.blocks] == [1, 1, 2]
    assert parsed.blocks[1].image_path == str((images / "fig.png").resolve())


def test_extract_doc_images(monkeypatch, isolated_data_dir):
    from app.config import get_settings

    root = Path(get_settings().processed_dir) / "v4-images"
    root.mkdir(parents=True, exist_ok=True)
    markdown = root / "document.md"
    markdown.write_text(
        "![Image block](doc:e1bcc06/tier:flash/page:1/block:1)\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        destination = Path(cmd[cmd.index("--output") + 1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(
        "app.ingest.parser.subprocess.run", fake_run
    )

    _extract_doc_images(markdown, root, "mineru", get_settings())

    text = markdown.read_text(encoding="utf-8")
    assert "images/mineru_1.png" in text
    assert (root / "images" / "mineru_1.png").is_file()
    assert calls and calls[0][1] == "read"
