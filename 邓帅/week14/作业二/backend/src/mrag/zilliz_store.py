"""Zilliz Cloud Serverless 向量库：collection 管理 + 向量读写检索。

schema（自建，参考项目缺失的部分）：
    db_id Int64 | file_name VarChar | text VarChar | text_vector(512, bge)
    | clip_text_vector(1024) | clip_image_vector(1024, jina-clip-v2)
三个向量字段均建 COSINE 索引；无图 chunk 的 clip_image_vector 填零向量
（零向量与任何真实归一化向量 COSINE 相似度不占优，不会误命中）。
"""

import os
from functools import lru_cache
from typing import Any

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient, connections

from mrag.config import get_settings
from mrag.embeddings import BGE_DIM, CLIP_DIM

TEXT_FIELD = "text_vector"
CLIP_TEXT_FIELD = "clip_text_vector"
CLIP_IMAGE_FIELD = "clip_image_vector"
OUTPUT_FIELDS = ["db_id", "file_name", "text"]


@lru_cache
def get_client() -> MilvusClient:
    s = get_settings()
    return MilvusClient(uri=s.zilliz_uri, user=s.zilliz_user_name, password=s.zilliz_password)


def _schema() -> CollectionSchema:
    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("db_id", DataType.INT64),
        FieldSchema("file_name", DataType.VARCHAR, max_length=512),
        FieldSchema("text", DataType.VARCHAR, max_length=65535),
        FieldSchema(TEXT_FIELD, DataType.FLOAT_VECTOR, dim=BGE_DIM),
        FieldSchema(CLIP_TEXT_FIELD, DataType.FLOAT_VECTOR, dim=CLIP_DIM),
        FieldSchema(CLIP_IMAGE_FIELD, DataType.FLOAT_VECTOR, dim=CLIP_DIM),
    ]
    return CollectionSchema(fields=fields, description="week14 multimodal rag chunks")


def ensure_collection(client: MilvusClient | None = None, name: str | None = None) -> str:
    """幂等建 collection（含三向量 COSINE 索引）并 load（serverless 检索前必须）。"""
    client = client or get_client()
    s = get_settings()
    name = name or s.zilliz_collection
    if not client.has_collection(name):
        client.create_collection(collection_name=name, schema=_schema())
        index_params = MilvusClient.prepare_index_params()
        for f in (TEXT_FIELD, CLIP_TEXT_FIELD, CLIP_IMAGE_FIELD):
            index_params.add_index(field_name=f, index_type="AUTOINDEX", metric_type="COSINE")
        client.create_index(collection_name=name, index_params=index_params)
    try:
        client.load_collection(name)
    except Exception:  # noqa: BLE001 - 已 load 时重复调用无害，个别后端报错可忽略
        pass
    return name


def upsert_chunks(rows: list[dict[str, Any]], client: MilvusClient | None = None) -> int:
    """写入 chunk 向量行（先按 db_id 清旧，保证重解析幂等）。返回写入条数。"""
    if not rows:
        return 0
    client = client or get_client()
    s = get_settings()
    name = s.zilliz_collection
    db_id = rows[0]["db_id"]
    delete_by_db_id(db_id, client=client)
    client.insert(collection_name=name, data=rows)
    client.flush(collection_name=name)
    return len(rows)


def delete_by_db_id(db_id: int, client: MilvusClient | None = None) -> None:
    client = client or get_client()
    s = get_settings()
    client.delete(collection_name=s.zilliz_collection, filter=f"db_id == {int(db_id)}")


def search(
    data: list[float],
    anns_field: str,
    top_k: int,
    client: MilvusClient | None = None,
) -> list[dict[str, Any]]:
    """向量检索，返回命中 [{db_id, file_name, text, score}]。"""
    client = client or get_client()
    s = get_settings()
    res = client.search(
        collection_name=s.zilliz_collection,
        data=[data],
        anns_field=anns_field,
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        search_params={"metric_type": "COSINE"},
    )
    hits = []
    for hit in res[0]:
        ent = hit.get("entity", {})
        hits.append(
            {
                "db_id": ent.get("db_id"),
                "file_name": ent.get("file_name"),
                "text": ent.get("text"),
                "score": hit.get("distance"),
            }
        )
    return hits


def close() -> None:
    """进程退出时清理连接。"""
    try:
        connections.disconnect("default")
    except Exception:  # noqa: BLE001 - 退出清理无需严格
        pass
    os.environ.pop("_mrag_zilliz_closed", None)  # no-op 占位，保持接口稳定
