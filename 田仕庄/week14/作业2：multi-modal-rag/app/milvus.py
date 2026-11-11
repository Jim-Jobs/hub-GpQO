"""Milvus 客户端与集合管理（懒加载，mock 模式下完全不连）。"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    settings = get_settings()
    if settings.mock_external:
        return None
    if not settings.milvus_uri:
        raise RuntimeError("MILVUS_URI is required when mock is disabled")
    with _client_lock:
        if _client is None:
            from pymilvus import MilvusClient

            _client = MilvusClient(
                uri=settings.milvus_uri,
                token=settings.milvus_token or "",
            )
        return _client


def ensure_collection() -> bool:
    """集合不存在时创建；mock 模式返回 True。"""
    settings = get_settings()
    if settings.mock_external:
        return True

    client = get_client()
    if client.has_collection(settings.milvus_collection):
        return True

    from pymilvus import DataType

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field(
        "text_vector", DataType.FLOAT_VECTOR, dim=settings.bge_dim
    )
    schema.add_field(
        "clip_text_vector", DataType.FLOAT_VECTOR, dim=settings.clip_dim
    )
    schema.add_field(
        "clip_image_vector", DataType.FLOAT_VECTOR, dim=settings.clip_dim
    )
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("db_id", DataType.INT64)
    schema.add_field("file_name", DataType.VARCHAR, max_length=512)
    schema.add_field("file_path", DataType.VARCHAR, max_length=1024)
    schema.add_field("page", DataType.VARCHAR, max_length=16)

    index_params = client.prepare_index_params()
    for field in ("text_vector", "clip_text_vector", "clip_image_vector"):
        index_params.add_index(
            field_name=field,
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

    client.create_collection(
        collection_name=settings.milvus_collection,
        schema=schema,
        index_params=index_params,
    )
    logger.info("created milvus collection: %s", settings.milvus_collection)
    return True


def replace_document_vectors(file_id: int, records: list[dict]) -> None:
    """先删旧向量再插入并 flush，保证幂等与立即可检索。"""
    settings = get_settings()
    if settings.mock_external:
        logger.debug(
            "mock milvus replace: file_id=%s records=%s", file_id, len(records)
        )
        return

    ensure_collection()
    client = get_client()
    client.delete(
        collection_name=settings.milvus_collection,
        filter=f"db_id == {file_id}",
    )
    if records:
        client.insert(
            collection_name=settings.milvus_collection,
            data=records,
        )
    client.flush(collection_name=settings.milvus_collection)


def delete_by_file_id(file_id: int) -> None:
    settings = get_settings()
    if settings.mock_external:
        return
    client = get_client()
    client.delete(
        collection_name=settings.milvus_collection,
        filter=f"db_id == {file_id}",
    )
    client.flush(collection_name=settings.milvus_collection)


def count_vectors() -> int:
    settings = get_settings()
    if settings.mock_external:
        return 0
    client = get_client()
    stats = client.get_collection_stats(settings.milvus_collection)
    return int(stats.get("row_count", 0))


def search_vectors(
    vector: list[float],
    anns_field: str,
    limit: int,
    *,
    file_ids: list[int] | None = None,
    retrieval_method: str = "bge",
) -> list[dict]:
    """单通道向量检索；COSINE 下 distance 越大越相似。"""
    settings = get_settings()
    if settings.mock_external:
        return []

    client = get_client()
    kwargs = {
        "collection_name": settings.milvus_collection,
        "data": [vector],
        "anns_field": anns_field,
        "limit": limit,
        "output_fields": [
            "text",
            "db_id",
            "file_name",
            "file_path",
            "page",
        ],
    }
    if file_ids:
        ids = ",".join(str(item) for item in file_ids)
        kwargs["filter"] = f"db_id in [{ids}]"

    client.load_collection(collection_name=settings.milvus_collection)
    results = client.search(**kwargs)
    hits: list[dict] = []
    for item in results[0]:
        entity = item.get("entity", {})
        hits.append(
            {
                "text": entity.get("text", ""),
                "file_id": entity.get("db_id"),
                "file_name": entity.get("file_name", ""),
                "file_path": entity.get("file_path", ""),
                "page": entity.get("page") or None,
                "score": item.get("distance"),
                "retrieval_method": retrieval_method,
            }
        )
    return hits


def reset_client() -> None:
    global _client
    with _client_lock:
        _client = None
