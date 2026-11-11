from __future__ import annotations

import io

import pytest
from PIL import Image

from app.errors import AppError


def _image_bytes(size=(64, 48), color="red", fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def test_normalize_converts_to_jpeg_and_limits_side(isolated_data_dir):
    from app.chat.normalize import normalize_images

    raw = _image_bytes(size=(2000, 1000))
    normalized = normalize_images([("a.png", raw)], "trace-normalize")

    assert len(normalized) == 1
    image = normalized[0]
    assert image.path.is_file()
    assert image.path.suffix == ".jpg"
    assert image.data_url.startswith("data:image/jpeg;base64,")
    assert max(image.width, image.height) <= 1280


def test_normalize_rejects_invalid_bytes(isolated_data_dir):
    from app.chat.normalize import normalize_images

    with pytest.raises(AppError) as exc:
        normalize_images([("a.png", b"not an image")], "trace-invalid")
    assert exc.value.code == "IMAGE_INVALID"


def test_normalize_rejects_too_many_images(isolated_data_dir):
    from app.chat.normalize import normalize_images

    raw = _image_bytes()
    with pytest.raises(AppError) as exc:
        normalize_images([("a.png", raw)] * 5, "trace-many")
    assert exc.value.code == "TOO_MANY_IMAGES"


def test_normalize_rejects_oversize(isolated_data_dir):
    from app.chat.normalize import normalize_images
    from app.config import get_settings

    settings = get_settings()
    original = settings.max_question_image_bytes
    settings.max_question_image_bytes = 10
    try:
        with pytest.raises(AppError) as exc:
            normalize_images([("a.png", _image_bytes())], "trace-large")
        assert exc.value.code == "IMAGE_TOO_LARGE"
    finally:
        settings.max_question_image_bytes = original
