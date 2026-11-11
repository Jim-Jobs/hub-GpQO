from __future__ import annotations

from pathlib import Path

from app.chat.normalize import NormalizedImage
from app.chat.parts import build_parts
from app.schemas import SourceItem


def _user_image(slot: int) -> NormalizedImage:
    return NormalizedImage(
        slot=slot,
        path=Path(f"img{slot}.jpg"),
        data_url=f"data:image/jpeg;base64,AAAA{slot}",
        width=10,
        height=10,
    )


def _types(parts: list[dict]) -> list[str]:
    return [part["type"] for part in parts]


def test_parts_keep_placeholder_order_and_labels():
    question = "对比 ![图1] 和 ![图2] 的差异"
    parts, used, dropped = build_parts(
        question,
        [_user_image(1), _user_image(2)],
        [],
        "",
        max_images=4,
    )

    assert _types(parts) == [
        "text",
        "text",
        "image_url",
        "text",
        "text",
        "image_url",
        "text",
    ]
    assert "图1" in parts[1]["text"]
    assert "图2" in parts[4]["text"]
    assert len(used) == 2
    assert all(item.source == "user" for item in used)
    assert dropped == 0


def test_parts_drop_images_over_budget():
    parts, used, dropped = build_parts(
        "看这两张 ![图1] ![图2]",
        [_user_image(1), _user_image(2)],
        [],
        "",
        max_images=1,
    )

    assert len(used) == 1
    assert dropped == 1
    assert _types(parts).count("image_url") == 1


def test_parts_append_unreferenced_images():
    parts, used, _ = build_parts(
        "这是什么",
        [_user_image(1)],
        [],
        "",
        max_images=4,
    )

    assert _types(parts).count("image_url") == 1
    assert used[0].source == "user"


def test_parts_add_retrieved_images_from_processed_dir(isolated_data_dir):
    from app.config import get_settings

    settings = get_settings()
    image_path = (
        Path(settings.processed_dir) / "doc" / "auto" / "images" / "fig.jpg"
    )
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\xff\xd8\xff\xd9")

    source = SourceItem(
        file_id=1,
        file_name="doc.pdf",
        file_path="uploads/doc.pdf",
        text_snippet="snippet",
        page="3",
        image_urls=["/files/processed/doc/auto/images/fig.jpg"],
        retrieval_method="bge",
    )
    parts, used, _ = build_parts(
        "问题", [], [source], "context", max_images=2
    )

    assert _types(parts).count("image_url") == 1
    assert any(item.source == "retrieved" for item in used)
    assert "doc.pdf" in "".join(
        part.get("text", "") for part in parts if part["type"] == "text"
    )
