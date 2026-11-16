"""MinerU 结果目录、解析页面和图片资源归一化测试。"""

import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pytest

from backend.app.core.config import Settings
from backend.app.documents.application.process_document import PermanentProcessingError
from backend.app.documents.domain.models import IngestEvent
from backend.app.documents.parsing.mineru_parser import parse_document
from backend.app.documents.parsing.result_reader import MineruOutputError, read_result


class FakeMineruClient:
    """返回预先制作的解析 ZIP，并记录传入的 PDF 和等待上限。"""

    def __init__(self, result_zip: bytes) -> None:
        """保存模拟 ZIP 内容和解析调用参数。"""
        self.result_zip = result_zip
        self.calls: list[tuple[str, float]] = []

    def parse_pdf(self, pdf_path, *, timeout_seconds: float) -> bytes:
        """模拟远端 Tasks API 成功并返回解析 ZIP。"""
        self.calls.append((pdf_path.name, timeout_seconds))
        return self.result_zip


def _event(document_id: str = "doc-1") -> IngestEvent:
    """建立指向 data/uploads/{document_id}.pdf 的 Kafka 事件。"""
    return IngestEvent(
        "event-1",
        document_id,
        "report.pdf",
        f"uploads/{document_id}.pdf",
        datetime.now(timezone.utc),
    )


def _zip_with_result(*, pages: list[dict] | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    """生成包含 MinerU default ZIP 标准文件名的测试产物。"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "structured_content.json",
            json.dumps({"pages": pages if pages is not None else [{"page_idx": 0, "blocks": [{"type": "text", "content": "目录正文"}]}]}),
        )
        archive.writestr("markdown.md", "# 标题\n\n目录正文")
        archive.writestr("images/page_0_image_0.png", b"png bytes")
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_parse_document_saves_normalized_pages_and_safe_image_assets(tmp_path) -> None:
    """解析器从 Tasks ZIP 提取文本页和图片，并把产物限制在文档目录。"""
    settings = Settings.from_env(
        {
            "RAG_DATA_ROOT": str(tmp_path),
            "RAG_MINERU_BASE_URL": "http://127.0.0.1:8000",
            "RAG_MINERU_TASK_TIMEOUT_SECONDS": "42",
        }
    )
    settings.initialize_data_directories()
    (settings.uploads_dir / "doc-1.pdf").write_bytes(b"%PDF-test")
    client = FakeMineruClient(_zip_with_result())

    parsed = parse_document(_event(), settings, client=client)

    assert client.calls == [("doc-1.pdf", 42)]
    assert parsed.document_id == "doc-1"
    assert parsed.markdown == "# 标题\n\n目录正文"
    assert [(page.page_number, page.text) for page in parsed.pages] == [(1, "目录正文")]
    assert parsed.pages[0].asset_ids == [parsed.assets[0].asset_id]
    assert parsed.assets[0].relative_path == "images/page_0_image_0.png"
    assert (settings.parsed_dir / "doc-1" / parsed.assets[0].relative_path).read_bytes() == b"png bytes"


def test_parse_document_rejects_zip_path_traversal(tmp_path) -> None:
    """MinerU ZIP 成员不能借由父目录路径写到文档目录之外。"""
    settings = Settings.from_env(
        {"RAG_DATA_ROOT": str(tmp_path), "RAG_MINERU_BASE_URL": "http://127.0.0.1:8000"}
    )
    settings.initialize_data_directories()
    (settings.uploads_dir / "doc-1.pdf").write_bytes(b"%PDF-test")
    result_zip = _zip_with_result(extra={"../../escaped.txt": b"outside"})

    with pytest.raises(PermanentProcessingError, match="越界路径"):
        parse_document(_event(), settings, client=FakeMineruClient(result_zip))

    assert not (tmp_path / "escaped.txt").exists()


def test_reader_rejects_empty_structured_result(tmp_path) -> None:
    """没有页面和图片的解析结果不能被当成成功文档。"""
    output_dir = tmp_path / "parsed" / "doc-1"
    output_dir.mkdir(parents=True)
    (output_dir / "structured_content.json").write_text('{"pages": []}', encoding="utf-8")

    with pytest.raises(MineruOutputError, match="解析结果为空"):
        read_result("doc-1", output_dir, [])


def test_parse_document_rejects_kafka_path_outside_uploads(tmp_path) -> None:
    """Kafka 任务路径不能读取 uploads 目录之外的服务器文件。"""
    settings = Settings.from_env(
        {"RAG_DATA_ROOT": str(tmp_path), "RAG_MINERU_BASE_URL": "http://127.0.0.1:8000"}
    )
    settings.initialize_data_directories()
    event = IngestEvent(
        "event-1", "doc-1", "report.pdf", "../private.pdf", datetime.now(timezone.utc)
    )

    with pytest.raises(PermanentProcessingError, match="安全的相对路径"):
        parse_document(event, settings, client=FakeMineruClient(_zip_with_result()))
