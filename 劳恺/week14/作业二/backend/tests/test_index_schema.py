"""ES 索引 mapping 单测 —— 这些字段一旦建错就要重建索引，改动成本最高。"""

from __future__ import annotations

from app.storage.index_schema import (
    INDEX_CHUNKS,
    INDEX_DOCUMENTS,
    INDEX_IMAGES,
    INDEX_SESSIONS,
    build_index_definitions,
)


def test_four_indices_defined() -> None:
    definitions = build_index_definitions()
    assert set(definitions) == {
        INDEX_DOCUMENTS,
        INDEX_CHUNKS,
        INDEX_IMAGES,
        INDEX_SESSIONS,
    }


def test_vector_dim_is_configurable() -> None:
    """dims 必须与嵌入模型输出维度一致，否则写入直接被 ES 拒绝。"""
    definitions = build_index_definitions(vector_dim=768)
    for index in (INDEX_CHUNKS, INDEX_IMAGES):
        vector = definitions[index]["mappings"]["properties"]["vector"]
        assert vector["dims"] == 768
        assert vector["similarity"] == "cosine"
        assert vector["index"] is True


def test_chunks_tokens_field_preserves_jieba_words() -> None:
    """jieba 预分词写入 tokens 字段。

    必须用 whitespace 分析器 —— ES 内置 standard 分析器按 UAX#29 把中文逐字切分
    （"第三季度" → 第/三/季/度），会把 jieba 的分词结果重新打散，jieba 就白做了。
    """
    properties = build_index_definitions()[INDEX_CHUNKS]["mappings"]["properties"]
    assert properties["tokens"] == {"type": "text", "analyzer": "jieba_tokens"}


def test_tokens_analyzer_is_whitespace_based() -> None:
    """自定义分析器 = whitespace 分词器 + lowercase（英文需小写化，否则 Qwen3 ≠ qwen3）。"""
    for body in build_index_definitions().values():
        analyzer = body["settings"]["analysis"]["analyzer"]["jieba_tokens"]
        assert analyzer["type"] == "custom"
        assert analyzer["tokenizer"] == "whitespace"
        assert analyzer["filter"] == ["lowercase"]


def test_no_analysis_ik_plugin_required() -> None:
    """不依赖 analysis-ik 插件（插件版本必须与 ES 版本严格一致，是部署坑）。"""
    for body in build_index_definitions().values():
        for analyzer in body["settings"]["analysis"]["analyzer"].values():
            assert "ik" not in str(analyzer).lower()


def test_chunk_images_is_nested() -> None:
    """图片是 chunk 的从属实体，必须用 nested，否则多值数组交叉展开导致过滤语义错误。"""
    properties = build_index_definitions()[INDEX_CHUNKS]["mappings"]["properties"]
    assert properties["images"]["type"] == "nested"
    assert "image_id" in properties["images"]["properties"]


def test_documents_index_has_status_and_error() -> None:
    properties = build_index_definitions()[INDEX_DOCUMENTS]["mappings"]["properties"]
    assert properties["status"] == {"type": "keyword"}
    assert properties["stage"] == {"type": "keyword"}
    assert properties["progress"] == {"type": "float"}
    assert properties["error"]["type"] == "text"


def test_bboxes_not_indexed() -> None:
    """bboxes 只用于回显图在页面上的位置，不参与检索。"""
    properties = build_index_definitions()[INDEX_CHUNKS]["mappings"]["properties"]
    assert properties["bboxes"] == {"type": "object", "enabled": False}


def test_single_node_no_replicas() -> None:
    """单节点 ES 下副本只会让集群长期 yellow。"""
    for body in build_index_definitions().values():
        assert body["settings"]["number_of_replicas"] == 0
