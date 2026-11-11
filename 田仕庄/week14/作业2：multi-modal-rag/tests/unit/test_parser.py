from __future__ import annotations

from pathlib import Path

from app.ingest.parser import ParsedBlock, parse_document


def test_mock_parser_writes_artifacts():
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    pdf = settings.uploads_dir / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 mock")

    parsed = parse_document(str(pdf), settings.processed_dir, settings)

    assert parsed.page_count == 3
    assert len(parsed.blocks) == 4
    assert any(block.image_path for block in parsed.blocks)

    doc_dir = Path(settings.processed_dir) / "doc" / "auto"
    assert (doc_dir / "content_list.json").is_file()
    assert (doc_dir / "images" / "fig1.png").is_file()


def test_mock_parser_rejects_non_mock_backend():
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    pdf = settings.uploads_dir / "doc2.pdf"
    pdf.write_bytes(b"%PDF-1.4 mock")

    original = settings.parse_backend
    settings.parse_backend = "mineru"
    try:
        try:
            parse_document(str(pdf), settings.processed_dir, settings)
        except Exception as exc:
            assert getattr(exc, "code", "") == "MINERU_NOT_INSTALLED"
        else:  # pragma: no cover
            raise AssertionError("expected ParseError")
    finally:
        settings.parse_backend = original


def test_parsed_block_defaults():
    block = ParsedBlock("text")
    assert block.page is None
    assert block.image_path is None
