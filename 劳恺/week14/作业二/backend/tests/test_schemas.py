"""内部模型的 ES 文档体单测。

**关键回归点**：`to_es()` 必须保留 ``chunk_id`` / ``image_id`` 字段 ——
``docs/02`` §2.2 的 mapping 声明了它们，检索层要用字段查询反查块。
曾经把 ``chunk_id`` pop 掉，导致检索层用 ``{"terms": {"chunk_id": [...]}}``
一条都查不到（表现为"检索永远返回 0 条"）。
"""

from __future__ import annotations

from app.schemas.index import (
    ChunkDoc,
    DocumentDoc,
    ImageDoc,
    chunk_id_for,
    image_id_for,
    new_doc_id,
    new_session_id,
    new_task_id,
)


def test_chunk_to_es_keeps_chunk_id() -> None:
    chunk = ChunkDoc(
        chunk_id="d_abc_c0007",
        doc_id="d_abc",
        doc_filename="财报.pdf",
        seq=7,
        page_start=1,
        page_end=2,
        text="正文",
        tokens="正文",
    )
    body = chunk.to_es()
    assert body["chunk_id"] == "d_abc_c0007"
    assert body["doc_id"] == "d_abc"


def test_image_to_es_keeps_image_id() -> None:
    image = ImageDoc(
        image_id="d_abc_p7_i1",
        doc_id="d_abc",
        doc_filename="财报.pdf",
        chunk_id="d_abc_c0007",
        page_no=7,
        idx=1,
        path="d_abc/d_abc_p7_i1.png",
        url="/static/images/d_abc/d_abc_p7_i1.png",
    )
    body = image.to_es()
    assert body["image_id"] == "d_abc_p7_i1"
    assert body["chunk_id"] == "d_abc_c0007"


def test_document_to_es_keeps_doc_id() -> None:
    doc = DocumentDoc(doc_id="d_abc", filename="a.pdf", file_path="/tmp/a.pdf")
    body = doc.to_es()
    assert body["doc_id"] == "d_abc"
    assert body["status"] == "pending"


def test_id_formats() -> None:
    """ID 规则（docs/02 §3.3、docs/03 §5.1）。"""
    assert chunk_id_for("d_abc", 7) == "d_abc_c0007"
    assert chunk_id_for("d_abc", 123) == "d_abc_c0123"
    assert image_id_for("d_abc", 7, 1) == "d_abc_p7_i1"


def test_generated_id_prefixes() -> None:
    assert new_doc_id().startswith("d_")
    assert len(new_doc_id()) == 14
    assert new_session_id().startswith("s_")
    assert new_task_id().startswith("t_")
    # 随机性
    assert new_doc_id() != new_doc_id()
