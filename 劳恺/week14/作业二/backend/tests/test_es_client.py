"""ES 客户端单测：只测不依赖真实 ES 的纯逻辑。"""

from __future__ import annotations

from app.storage.es_client import build_bulk_operations


def test_bulk_operations_alternate_action_and_source() -> None:
    """elasticsearch-py 的 bulk 要求 action 与 source 严格交替。"""
    operations = build_bulk_operations(
        "mmrag-chunks",
        [("d_a_c0000", {"text": "第一块"}), ("d_a_c0001", {"text": "第二块"})],
    )
    assert len(operations) == 4
    assert operations[0] == {"index": {"_index": "mmrag-chunks", "_id": "d_a_c0000"}}
    assert operations[1] == {"text": "第一块"}
    assert operations[2]["index"]["_id"] == "d_a_c0001"
    assert operations[3] == {"text": "第二块"}


def test_bulk_operations_preserves_order() -> None:
    """顺序必须稳定 —— chunk_id 由序号决定，写入顺序错乱会导致块与图片错位。"""
    items = [(f"d_a_c{i:04d}", {"seq": i}) for i in range(5)]
    operations = build_bulk_operations("mmrag-chunks", items)
    ids = [op["index"]["_id"] for op in operations if "index" in op]
    assert ids == [doc_id for doc_id, _ in items]


def test_bulk_operations_empty() -> None:
    assert build_bulk_operations("mmrag-chunks", []) == []
