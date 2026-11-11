from __future__ import annotations


def test_mock_milvus_is_noop(isolated_data_dir):
    from app.milvus import (
        count_vectors,
        delete_by_file_id,
        ensure_collection,
        replace_document_vectors,
    )

    assert ensure_collection() is True

    replace_document_vectors(1, [{"text": "a"}])
    delete_by_file_id(1)

    assert count_vectors() == 0
