"""Elasticsearch 索引 mapping 定义。

对应文档：``docs/02-数据模型.md`` §2。

三个不能改的取舍：

1. **``tokens`` 字段用自定义的 ``jieba_tokens`` 分析器** —— 中文 BM25 的核心。
   入库时用 jieba 切词并用空格连接，ES 用 **whitespace** 分词器切分，词边界被完整保留。

   ⚠️ 这里**不能用内置 ``standard`` 分析器**：ES 的 standard 分析器按 UAX#29 把中文
   **逐字切分**（"第三季度" → 第 / 三 / 季 / 度），会把 jieba 的分词结果重新打散，
   jieba 预分词就等于白做，BM25 路退化成单字匹配。
   用 whitespace + lowercase 既保留了词，又对英文做小写化（``Qwen3`` 与 ``qwen3`` 同词）。
   这样完全不需要 ``analysis-ik`` 插件，规避"插件版本必须与 ES 版本严格一致"的部署坑。

   > 若将来改用 IK 插件，**必须同时去掉 jieba 预分词** —— 否则会被二次切分，
   > 两套方案对同一个字段是互斥的，不能叠加。
2. **``text`` 字段保留原文**（``standard`` 分析器），只用于高亮与展示，**不参与 BM25 检索**。
3. **图片提到独立索引 ``mmrag-images``** —— ES 的 ``dense_vector`` kNN 不支持在 ``nested``
   字段上高效检索。第三路「图片向量召回」直接对独立索引做 kNN，再按 ``chunk_id`` 反查文字块。
"""

from __future__ import annotations

from typing import Any

# 索引名（与 docs/07 §6 的 ES_INDEX_* 环境变量默认值一致）
INDEX_DOCUMENTS = "mmrag-documents"
INDEX_CHUNKS = "mmrag-chunks"
INDEX_IMAGES = "mmrag-images"
INDEX_SESSIONS = "mmrag-sessions"

#: ``tokens`` 字段专用的分析器名
TOKENS_ANALYZER = "jieba_tokens"


def _analysis_settings() -> dict[str, Any]:
    """索引级分析器定义（对应 ES settings.analysis）。"""
    return {
        "analysis": {
            "analyzer": {
                TOKENS_ANALYZER: {
                    "type": "custom",
                    "tokenizer": "whitespace",
                    "filter": ["lowercase"],
                }
            }
        }
    }


def _chunk_mapping(vector_dim: int) -> dict[str, Any]:
    return {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_filename": {"type": "keyword"},
            "seq": {"type": "integer"},
            "page_start": {"type": "integer"},
            "page_end": {"type": "integer"},
            "text": {
                "type": "text",
                "analyzer": "standard",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            # jieba 预分词后空格连接；必须用 whitespace 分析器，否则会被 standard 逐字打散
            "tokens": {"type": "text", "analyzer": TOKENS_ANALYZER},
            "block_types": {"type": "keyword"},
            # 只用于回显"图在页面上的位置"，不参与检索
            "bboxes": {"type": "object", "enabled": False},
            "images": {
                "type": "nested",
                "properties": {
                    "image_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "page_no": {"type": "integer"},
                    "idx": {"type": "integer"},
                    "path": {"type": "keyword"},
                    "url": {"type": "keyword"},
                    "caption": {"type": "text"},
                    "bbox": {"type": "float", "index": False},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
            },
            "vector": {
                "type": "dense_vector",
                "dims": vector_dim,
                "index": True,
                "similarity": "cosine",
            },
            "created_at": {"type": "date"},
        }
    }


def _image_mapping(vector_dim: int) -> dict[str, Any]:
    return {
        "properties": {
            "image_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_filename": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "page_no": {"type": "integer"},
            "idx": {"type": "integer"},
            "path": {"type": "keyword"},
            "url": {"type": "keyword"},
            "caption": {"type": "text"},
            "tokens": {"type": "text", "analyzer": TOKENS_ANALYZER},
            "bbox": {"type": "float", "index": False},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "vector": {
                "type": "dense_vector",
                "dims": vector_dim,
                "index": True,
                "similarity": "cosine",
            },
            "created_at": {"type": "date"},
        }
    }


def _document_mapping() -> dict[str, Any]:
    return {
        "properties": {
            "doc_id": {"type": "keyword"},
            "filename": {"type": "keyword"},
            "file_path": {"type": "keyword", "index": False},
            "content_hash": {"type": "keyword"},
            "size_bytes": {"type": "long"},
            "page_count": {"type": "integer"},
            "status": {"type": "keyword"},
            "progress": {"type": "float"},
            "stage": {"type": "keyword"},
            "error": {
                "type": "text",
                "fields": {"raw": {"type": "keyword", "ignore_above": 1024}},
            },
            "chunk_count": {"type": "integer"},
            "image_count": {"type": "integer"},
            "parser": {"type": "keyword"},
            "parse_duration_ms": {"type": "long"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "finished_at": {"type": "date"},
        }
    }


def _session_mapping() -> dict[str, Any]:
    return {
        "properties": {
            "session_id": {"type": "keyword"},
            # 会话消息整体读写，不做检索
            "messages": {"type": "object", "enabled": False},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
        }
    }


def build_index_definitions(vector_dim: int = 1024) -> dict[str, dict[str, Any]]:
    """构造四个索引的创建参数。

    ``number_of_replicas`` 固定为 0：单节点 ES 下副本只会让集群长期 yellow。
    ``vector_dim`` 必须与实际嵌入模型输出维度一致，否则写入直接被 ES 拒绝。
    """
    settings = {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        **_analysis_settings(),
    }
    return {
        INDEX_DOCUMENTS: {"settings": settings, "mappings": _document_mapping()},
        INDEX_CHUNKS: {"settings": settings, "mappings": _chunk_mapping(vector_dim)},
        INDEX_IMAGES: {"settings": settings, "mappings": _image_mapping(vector_dim)},
        INDEX_SESSIONS: {"settings": settings, "mappings": _session_mapping()},
    }


INDEX_DEFINITIONS = build_index_definitions()
"""默认 1024 维的索引定义。启动时若 ``ES_VECTOR_DIM`` 与此不同，用
``build_index_definitions(settings.es_vector_dim)`` 重新构造。"""
