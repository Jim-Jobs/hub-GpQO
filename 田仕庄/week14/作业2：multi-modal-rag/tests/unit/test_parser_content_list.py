from __future__ import annotations

import json
from pathlib import Path

from app.ingest.parser import _parse_content_list


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")


def test_content_list_pages_and_image(isolated_data_dir):
    from app.config import get_settings

    root = Path(get_settings().processed_dir) / "manual"
    image = root / "images" / "fig.png"
    _write_png(image)

    items = [
        {"type": "text", "page_idx": 0, "text": "first page"},
        {
            "type": "image",
            "page_idx": 1,
            "img_path": "images/fig.png",
            "image_caption": ["figure caption"],
        },
        {
            "type": "table",
            "page_idx": 2,
            "table_body": "<table>data</table>",
            "table_caption": ["table caption"],
        },
    ]
    content_list = root / "content_list.json"
    content_list.write_text(json.dumps(items), encoding="utf-8")

    parsed = _parse_content_list(content_list)

    assert parsed.page_count == 3
    assert [block.page for block in parsed.blocks] == [1, 2, 3]
    assert parsed.blocks[1].image_path == str(image.resolve())
    assert "figure caption" in parsed.blocks[1].text
    assert "table caption" in parsed.blocks[2].text


def test_content_list_without_blocks_raises(isolated_data_dir):
    from app.config import get_settings

    root = Path(get_settings().processed_dir) / "empty"
    root.mkdir(parents=True, exist_ok=True)
    content_list = root / "content_list.json"
    content_list.write_text("[]", encoding="utf-8")

    try:
        _parse_content_list(content_list)
    except Exception as exc:
        assert getattr(exc, "code", "") == "PARSE_OUTPUT_MISSING"
    else:  # pragma: no cover
        raise AssertionError("expected ParseError")
