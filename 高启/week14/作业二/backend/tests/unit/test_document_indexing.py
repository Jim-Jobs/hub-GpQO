"""文档切块、向量写入、摘要和资料库概览行为测试。"""

from datetime import datetime, timezone
import threading
import sys
from types import SimpleNamespace

import pytest

from backend.app.documents.domain.models import (
    DocumentRecord,
    DocumentSummary,
    DocumentStatus,
    ImageAsset,
    IngestEvent,
    ParsedDocument,
    ParsedPage,
    VectorRecord,
)
from backend.app.documents.indexing.chunk_builder import build_chunks
from backend.app.documents.indexing.corpus_profile import update_corpus_profile
from backend.app.documents.indexing.embed_chunks import embed_chunks
from backend.app.documents.indexing.pipeline import DocumentIndexer
from backend.app.documents.indexing.write_vectors import write_vectors
from backend.app.core.config import Settings
from backend.app.documents.application.process_document import RetryableProcessingError
from backend.app.documents.application.process_document import handle_ingest_event
from backend.app.integrations.errors import EmbeddingError, DocumentStoreError, VectorStoreError
from backend.app.integrations.embedding_client import OpenAIEmbeddingClient
from backend.app.integrations.milvus_store import MilvusChunkStore
from backend.app.integrations.redis_document_store import RedisDocumentStore
from backend.app.integrations.summary_client import OpenAIDocumentSummaryClient
from backend.tests.unit.support import MemoryDocumentStore


class FixedEmbeddingClient:
    """记录批量输入并返回指定维数的测试向量。"""

    def __init__(self, dimension: int = 1024) -> None:
        """保存向量维度和已收到的文本批次。"""
        self.dimension = dimension
        self.batches: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """记录一批文本并为每条文本生成固定维数向量。"""
        self.batches.append(texts)
        return [[0.1] * self.dimension for _ in texts]


class FixedVectorStore:
    """收集 upsert 请求或模拟 Milvus 写入故障。"""

    def __init__(self, *, fail: bool = False) -> None:
        """初始化向量记录列表和失败开关。"""
        self.records: list[VectorRecord] = []
        self.fail = fail

    def upsert_chunks(self, records: list[VectorRecord]) -> None:
        """追加本次写入请求；失败模式用于验证错误转换。"""
        if self.fail:
            raise VectorStoreError("Milvus 不可用")
        self.records.extend(records)


class TransactionalMemoryRedis:
    """提供线程安全 key/value、扫描和事务管道的 Redis 替身。"""

    def __init__(self) -> None:
        """初始化空 key/value 表和互斥锁。"""
        self.values: dict[str, str] = {}
        self.lock = threading.RLock()
        self.fail_transaction = False

    def set(self, key: str, value: str) -> None:
        """保存字符串值。"""
        with self.lock:
            self.values[key] = value

    def get(self, key: str) -> str | None:
        """读取字符串值，不存在时返回 None。"""
        with self.lock:
            return self.values.get(key)

    def scan_iter(self, *, match: str):
        """返回符合 key 前缀的快照，模拟 Redis scan_iter。"""
        prefix = match.removesuffix("*")
        with self.lock:
            return iter([key for key in self.values if key.startswith(prefix)])

    def pipeline(self, *, transaction: bool):
        """创建记录 set 操作并在 execute 时提交的事务替身。"""
        assert transaction is True
        return MemoryPipeline(self)


def test_redis_document_store_round_trips_document_status() -> None:
    """文档状态继续按 document_id 保存，并能从 JSON 还原。"""
    store = RedisDocumentStore(TransactionalMemoryRedis())
    record = DocumentRecord("doc-round-trip", "report.pdf", DocumentStatus.PENDING)

    store.save(record)

    assert store.get(record.document_id) == record


class MemoryPipeline:
    """在单次 execute 中应用已排队的 Redis 字符串写入。"""

    def __init__(self, redis: TransactionalMemoryRedis) -> None:
        """保存 Redis 替身并准备空操作队列。"""
        self.redis = redis
        self.operations: list[tuple[str, str]] = []

    def __enter__(self):
        """允许使用 Redis pipeline 的 with 语法。"""
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        """保持 pipeline 上下文退出时不隐式提交。"""

    def set(self, key: str, value: str) -> None:
        """把字符串写入排入事务队列。"""
        self.operations.append((key, value))

    def execute(self) -> None:
        """在同一临界区内提交全部写入或模拟事务失败。"""
        with self.redis.lock:
            if self.redis.fail_transaction:
                raise ConnectionError("模拟事务失败")
            self.redis.values.update(self.operations)


def _parsed_document() -> ParsedDocument:
    """建立包含长文本和页级图片引用的解析结果。"""
    return ParsedDocument(
        document_id="doc-1",
        markdown="# 标题\n\n目录与正文",
        pages=[
            ParsedPage(1, "甲乙丙丁。\n\n" + "检索文本。" * 300, ["asset-1"]),
            ParsedPage(2, "   ", ["asset-2"]),
        ],
        assets=[ImageAsset("asset-1", "doc-1", "images/p1.png", 1)],
    )


def test_build_chunks_keeps_page_images_and_stable_ids() -> None:
    """切块限制长度、保留原页图片关联，并为同一输入生成稳定 ID。"""
    parsed = _parsed_document()

    chunks = build_chunks(parsed)

    assert chunks
    assert all(chunk.page_number == 1 for chunk in chunks)
    assert all(chunk.asset_ids == ["asset-1"] for chunk in chunks)
    assert all(len(chunk.text) <= 800 for chunk in chunks)
    assert chunks[0].text[-120:] == chunks[1].text[:120]
    assert [chunk.chunk_id for chunk in chunks] == [
        chunk.chunk_id for chunk in build_chunks(parsed)
    ]
    assert all(len(chunk.chunk_id) == 64 for chunk in chunks)


def test_build_chunks_ignores_empty_pages_without_losing_other_pages() -> None:
    """空白页不生成空向量块，其他页码仍按原页码保留。"""
    parsed = ParsedDocument("doc-2", "", [ParsedPage(4, "  \n ", []), ParsedPage(5, "正文", [])])

    chunks = build_chunks(parsed)

    assert [(chunk.page_number, chunk.text) for chunk in chunks] == [(5, "正文")]


def test_embed_chunks_rejects_wrong_vector_count_and_dimension() -> None:
    """Embedding 响应数量或 BGE-M3 向量维度不符时立即报错。"""
    chunks = build_chunks(ParsedDocument("doc-3", "", [ParsedPage(1, "正文", [])]))

    with pytest.raises(EmbeddingError, match="1024"):
        embed_chunks(chunks, FixedEmbeddingClient(dimension=768))

    class WrongCountClient:
        """返回少于输入文本数量的 embedding 条目。"""

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            """返回空向量批次触发数量检查。"""
            return []

    with pytest.raises(EmbeddingError, match="数量"):
        embed_chunks(chunks, WrongCountClient())


def test_embed_chunks_skips_empty_text_batches() -> None:
    """没有文本块时返回空结果，不调用 embedding 服务。"""
    client = FixedEmbeddingClient()

    result = embed_chunks([], client)

    assert result == []
    assert client.batches == []


def test_write_vectors_upserts_stable_records_and_wraps_store_failure() -> None:
    """向量写入使用 embedding 记录，Milvus 故障向上转换为可识别错误。"""
    chunks = build_chunks(ParsedDocument("doc-4", "", [ParsedPage(1, "正文", [])]))
    embedding_client = FixedEmbeddingClient()
    vector_store = FixedVectorStore()

    vectors = write_vectors(chunks, embedding_client, vector_store)

    assert len(vectors) == 1
    assert vectors[0].chunk.chunk_id == vector_store.records[0].chunk.chunk_id
    assert embedding_client.batches == [["正文"]]

    with pytest.raises(VectorStoreError, match="Milvus"):
        write_vectors(chunks, FixedEmbeddingClient(), FixedVectorStore(fail=True))


def test_update_corpus_profile_uses_only_completed_document_summaries() -> None:
    """资料库概览按稳定顺序汇总 completed 文档，不包含失败或处理中记录。"""
    records = [
        DocumentRecord("b", "b.pdf", DocumentStatus.COMPLETED, title="乙", summary="乙摘要"),
        DocumentRecord("a", "a.pdf", DocumentStatus.PARSING, title="甲", summary="未完成"),
        DocumentRecord("c", "c.pdf", DocumentStatus.FAILED, title="丙", summary="失败摘要"),
    ]

    profile = update_corpus_profile(records)

    assert profile.document_count == 1
    assert "乙" in profile.summary
    assert "未完成" not in profile.summary
    assert "失败摘要" not in profile.summary
    assert update_corpus_profile([records[0], records[0]]).document_count == 1


def test_milvus_store_upsert_replaces_the_same_stable_chunk_id() -> None:
    """重复写入相同 chunk_id 时调用 Milvus upsert，主键记录保持唯一。"""

    class FakeMilvusClient:
        """将 upsert 写入按 chunk_id 覆盖的内存字典。"""

        def __init__(self) -> None:
            """初始化向量表和写入调用次数。"""
            self.rows: dict[str, dict] = {}
            self.calls = 0

        def upsert(self, *, collection_name: str, data: list[dict]) -> None:
            """按每行主键覆盖记录并累计调用次数。"""
            assert collection_name == "rag_chunks"
            self.calls += 1
            self.rows.update({row["chunk_id"]: row for row in data})

    chunks = build_chunks(
        ParsedDocument("doc-5", "", [ParsedPage(1, "正文", ["asset-5"])])
    )
    vectors = embed_chunks(chunks, FixedEmbeddingClient())
    client = FakeMilvusClient()
    store = MilvusChunkStore(client)

    store.upsert_chunks(vectors)
    store.upsert_chunks(vectors)

    assert client.calls == 2
    assert len(client.rows) == 1
    row = client.rows[chunks[0].chunk_id]
    assert row["document_id"] == "doc-5"
    assert row["page_number"] == 1
    assert row["chunk_index"] == 0
    assert row["text"] == "正文"
    assert row["asset_ids"] == ["asset-5"]
    assert len(row["embedding"]) == 1024


def test_milvus_store_converts_client_write_failure() -> None:
    """Milvus 原始异常经适配器转换成统一 VectorStoreError。"""

    class FailingMilvusClient:
        """模拟 upsert 过程中 Milvus RPC 抛错。"""

        def upsert(self, *, collection_name: str, data: list[dict]) -> None:
            """抛出底层写入故障。"""
            raise ConnectionError("offline")

    vector = VectorRecord(
        build_chunks(ParsedDocument("doc-6", "", [ParsedPage(1, "正文", [])]))[0],
        [0.1] * 1024,
    )

    with pytest.raises(VectorStoreError, match="写入失败"):
        MilvusChunkStore(FailingMilvusClient()).upsert_chunks([vector])


def test_milvus_search_filters_completed_documents_and_maps_sources() -> None:
    """向量检索只接收完成文档过滤条件，并保留 chunk、图片和距离字段。"""

    class SearchingMilvusClient:
        """记录检索参数并返回一条具有来源元数据的命中。"""

        def __init__(self) -> None:
            """初始化尚未调用的搜索参数。"""
            self.options = None

        def search(self, **options) -> list[list[dict]]:
            """返回 Milvus SDK 形状的 Top-K 命中结果。"""
            self.options = options
            return [[{
                "id": "chunk-1",
                "distance": 0.87,
                "entity": {
                    "document_id": "doc-complete",
                    "page_number": 4,
                    "chunk_index": 2,
                    "text": "检索证据",
                    "asset_ids": ["asset-1"],
                },
            }]]

    client = SearchingMilvusClient()
    store = MilvusChunkStore(client)

    hits = store.search_chunks([0.1] * 1024, ["doc-complete"], 5)

    assert client.options["filter"] == "document_id in {document_ids}"
    assert client.options["filter_params"] == {"document_ids": ["doc-complete"]}
    assert client.options["limit"] == 5
    assert hits[0].chunk.chunk_id == "chunk-1"
    assert hits[0].chunk.document_id == "doc-complete"
    assert hits[0].chunk.page_number == 4
    assert hits[0].chunk.asset_ids == ["asset-1"]
    assert hits[0].score == 0.87


def test_milvus_search_skips_empty_document_scope() -> None:
    """没有已完成文档时不向 Milvus 发出无范围的全库搜索。"""

    class UnexpectedMilvusClient:
        """如果发生搜索就让测试立即失败的客户端替身。"""

        def search(self, **options) -> list[list[dict]]:
            """拒绝测试不允许执行的全库检索。"""
            raise AssertionError("不应执行没有文档范围的检索")

    hits = MilvusChunkStore(UnexpectedMilvusClient()).search_chunks([0.1] * 1024, [], 5)

    assert hits == []


def test_milvus_path_reuses_client_and_creates_expected_schema(tmp_path, monkeypatch) -> None:
    """相同数据库路径只创建一个 client，并声明所有检索与来源字段。"""

    class FakeSchema:
        """记录 collection schema 中的字段定义。"""

        def __init__(self) -> None:
            """初始化空字段定义列表。"""
            self.fields: list[tuple[str, dict]] = []

        def add_field(self, *, field_name: str, **options) -> None:
            """收集一个字段名称及其类型约束。"""
            self.fields.append((field_name, options))

    class FakeIndexParams:
        """记录向量索引的字段、类型和距离度量。"""

        def __init__(self) -> None:
            """初始化空索引定义列表。"""
            self.indexes: list[dict] = []

        def add_index(self, **options) -> None:
            """保存一条索引配置。"""
            self.indexes.append(options)

    class FakeMilvusClient:
        """记录数据库客户端创建和 collection 初始化操作。"""

        instances = []

        def __init__(self, *, uri: str) -> None:
            """保存数据库路径并记录本次 client 创建。"""
            self.uri = uri
            self.created_schema = None
            self.index_params = FakeIndexParams()
            self.instances.append(self)

        def has_collection(self, *, collection_name: str) -> bool:
            """模拟首次运行时 collection 尚不存在。"""
            return False

        def create_schema(self, **options) -> FakeSchema:
            """返回可记录字段的 schema 替身。"""
            assert options == {"auto_id": False, "enable_dynamic_field": False}
            self.created_schema = FakeSchema()
            return self.created_schema

        def create_collection(self, *, collection_name: str, schema: FakeSchema) -> None:
            """验证创建 collection 时使用预期名称和 schema。"""
            assert collection_name == "rag_chunks"
            self.created_schema = schema

        def prepare_index_params(self) -> FakeIndexParams:
            """返回记录索引定义的对象。"""
            return self.index_params

        def create_index(self, *, collection_name: str, index_params: FakeIndexParams) -> None:
            """确认向量索引绑定到目标 collection。"""
            assert collection_name == "rag_chunks"
            assert index_params is self.index_params

        def load_collection(self, *, collection_name: str) -> None:
            """确认初始化后载入目标 collection。"""
            assert collection_name == "rag_chunks"

    class FakeDataType:
        """提供 Milvus SDK schema 所需的数据类型常量。"""

        VARCHAR = "VARCHAR"
        INT64 = "INT64"
        ARRAY = "ARRAY"
        FLOAT_VECTOR = "FLOAT_VECTOR"

    monkeypatch.setitem(
        sys.modules,
        "pymilvus",
        SimpleNamespace(DataType=FakeDataType, MilvusClient=FakeMilvusClient),
    )
    path = tmp_path / "milvus" / "milvus_lite.db"

    first = MilvusChunkStore.from_path(path)
    second = MilvusChunkStore.from_path(path)

    assert first is second
    assert len(FakeMilvusClient.instances) == 1
    fields = {name: options for name, options in FakeMilvusClient.instances[0].created_schema.fields}
    assert set(fields) == {
        "chunk_id",
        "document_id",
        "page_number",
        "chunk_index",
        "text",
        "asset_ids",
        "embedding",
    }
    assert fields["embedding"]["dim"] == 1024
    assert FakeMilvusClient.instances[0].index_params.indexes[0]["metric_type"] == "COSINE"


def test_milvus_store_batches_large_document_writes() -> None:
    """大文档拆为最多 256 条一批，部分批次失败后可幂等重放。"""

    class BatchingMilvusClient:
        """记录批次大小并按主键模拟 Milvus upsert。"""

        def __init__(self) -> None:
            """初始化主键表、批次记录和失败开关。"""
            self.rows: dict[str, dict] = {}
            self.batch_sizes: list[int] = []
            self.fail_at: int | None = None

        def upsert(self, *, collection_name: str, data: list[dict]) -> None:
            """按配置在指定批次失败或执行主键覆盖。"""
            self.batch_sizes.append(len(data))
            if self.fail_at == len(self.batch_sizes):
                raise ConnectionError("批次故障")
            self.rows.update({row["chunk_id"]: row for row in data})

    chunks = [
        build_chunks(ParsedDocument(f"doc-{index}", "", [ParsedPage(1, "正文", [])]))[0]
        for index in range(300)
    ]
    vectors = [VectorRecord(chunk, [0.1] * 1024) for chunk in chunks]
    client = BatchingMilvusClient()
    store = MilvusChunkStore(client)
    client.fail_at = 2

    with pytest.raises(VectorStoreError, match="写入失败"):
        store.upsert_chunks(vectors)
    client.fail_at = None
    client.batch_sizes.clear()
    store.upsert_chunks(vectors)

    assert client.batch_sizes == [256, 44]
    assert len(client.rows) == 300


def test_redis_completion_serializes_profile_updates_and_excludes_failed_docs() -> None:
    """并发完成两个文档时概览保留两条摘要，重投同一文档不增加计数。"""
    redis = TransactionalMemoryRedis()
    store = RedisDocumentStore(redis)
    records = [
        DocumentRecord("doc-a", "a.pdf", DocumentStatus.COMPLETED, title="甲", summary="甲摘要"),
        DocumentRecord("doc-b", "b.pdf", DocumentStatus.COMPLETED, title="乙", summary="乙摘要"),
    ]
    threads = [threading.Thread(target=store.complete, args=(record,)) for record in records]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    store.complete(records[0])

    profile = store.get_corpus_profile()
    assert profile.document_count == 2
    assert "甲摘要" in profile.summary and "乙摘要" in profile.summary
    assert store.get("doc-a").status is DocumentStatus.COMPLETED


def test_redis_completion_transaction_failure_keeps_document_uncompleted() -> None:
    """Redis 事务失败时状态和概览均未写入，可由 Kafka 安全重投。"""
    redis = TransactionalMemoryRedis()
    store = RedisDocumentStore(redis)
    record = DocumentRecord("doc-z", "z.pdf", DocumentStatus.COMPLETED, title="丙", summary="摘要")
    redis.fail_transaction = True

    with pytest.raises(DocumentStoreError, match="写入失败"):
        store.complete(record)

    assert store.get("doc-z") is None
    assert store.get_corpus_profile() is None


def test_openai_summary_client_returns_validated_title_and_summary() -> None:
    """Qwen3-VL 的 JSON 响应被校验并转为文档摘要数据契约。"""

    class FakeSummaryClient:
        """提供 OpenAI SDK chat completion 的最小替身。"""

        class chat:
            """模拟 SDK 的 chat completions 命名空间。"""

            class completions:
                """模拟 SDK 的 completion 创建方法。"""

                @staticmethod
                def create(**request):
                    """断言请求包含标题和正文后返回稳定 JSON。"""
                    assert request["temperature"] == 0
                    assert "# 标题" in request["messages"][1]["content"]
                    assert "页面正文" in request["messages"][1]["content"]

                    class Message:
                        """承载模型文本响应。"""

                        content = '{"title":"主题","summary":"主要内容"}'

                    class Choice:
                        """承载一条 chat completion 选择。"""

                        message = Message()

                    class Response:
                        """模拟带 choices 的 SDK 响应。"""

                        choices = [Choice()]

                    return Response()

    parsed = ParsedDocument("doc-7", "# 标题\n\n目录", [ParsedPage(1, "页面正文", [])])

    result = OpenAIDocumentSummaryClient("http://localhost/v1", "Qwen", client=FakeSummaryClient()).summarize(parsed)

    assert result == DocumentSummary("主题", "主要内容")


def test_openai_embedding_client_batches_requests_and_restores_input_order() -> None:
    """OpenAI SDK 适配器分批调用，并依响应 index 恢复文本对应顺序。"""

    class FakeEmbeddings:
        """模拟返回乱序 embedding 条目的服务端响应。"""

        def __init__(self) -> None:
            """记录每批输入的文本。"""
            self.requests: list[list[str]] = []

        def create(self, *, model: str, input: list[str]):
            """返回按 index 逆序排列的固定向量条目。"""
            assert model == "bge-m3"
            self.requests.append(input)
            data = [
                SimpleNamespace(index=index, embedding=[float(index)])
                for index in reversed(range(len(input)))
            ]
            return SimpleNamespace(data=data)

    sdk = SimpleNamespace(embeddings=FakeEmbeddings())
    client = OpenAIEmbeddingClient(
        "http://embedding/v1", "bge-m3", client=sdk, batch_size=2
    )

    vectors = client.embed_texts(["甲", "乙", "丙"])

    assert sdk.embeddings.requests == [["甲", "乙"], ["丙"]]
    assert vectors == [[0.0], [1.0], [0.0]]


def test_document_indexer_writes_vectors_before_returning_summary(tmp_path) -> None:
    """只有向量写入成功后才返回摘要供 completed 状态持久化。"""
    parsed = ParsedDocument("doc-8", "# 标题", [ParsedPage(1, "正文", [])])
    event = IngestEvent(
        "event-8", "doc-8", "report.pdf", "uploads/doc-8.pdf", datetime.now(timezone.utc)
    )
    settings = Settings.from_env(
        {
            "RAG_DATA_ROOT": str(tmp_path),
            "RAG_EMBEDDING_BASE_URL": "http://embedding/v1",
            "RAG_CHAT_BASE_URL": "http://chat/v1",
        }
    )
    vector_store = FixedVectorStore()

    class OrderedSummaryClient:
        """确保摘要步骤只能在向量写入之后执行。"""

        def summarize(self, document: ParsedDocument) -> DocumentSummary:
            """检查向量记录已写入，再返回固定摘要。"""
            assert vector_store.records
            return DocumentSummary("标题", "摘要")

    indexer = DocumentIndexer(
        settings,
        embedding_client=FixedEmbeddingClient(),
        vector_store=vector_store,
        summary_client=OrderedSummaryClient(),
        parser=lambda _event, _settings: parsed,
    )

    result = indexer(event)

    assert result == DocumentSummary("标题", "摘要")
    assert len(vector_store.records) == 1


def test_document_indexer_classifies_indexing_failures_for_bounded_retry(tmp_path) -> None:
    """Embedding 服务故障进入既有有限重试生命周期。"""
    event = IngestEvent(
        "event-9", "doc-9", "report.pdf", "uploads/doc-9.pdf", datetime.now(timezone.utc)
    )
    settings = Settings.from_env(
        {
            "RAG_DATA_ROOT": str(tmp_path),
            "RAG_EMBEDDING_BASE_URL": "http://embedding/v1",
            "RAG_CHAT_BASE_URL": "http://chat/v1",
        }
    )

    class FailingEmbeddingClient:
        """模拟 embedding 服务不可用。"""

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            """抛出统一的 embedding 服务错误。"""
            raise EmbeddingError("offline")

    indexer = DocumentIndexer(
        settings,
        embedding_client=FailingEmbeddingClient(),
        vector_store=FixedVectorStore(),
        summary_client=OpenAIDocumentSummaryClient("http://chat/v1", "Qwen", client=object()),
        parser=lambda _event, _settings: ParsedDocument(
            "doc-9", "", [ParsedPage(1, "正文", [])]
        ),
    )

    with pytest.raises(RetryableProcessingError, match="暂时失败"):
        indexer(event)


def test_ingest_handler_persists_summary_before_acknowledgeable_completion(tmp_path) -> None:
    """文档摘要与 completed 状态一起交给存储层完成后才返回成功。"""
    store = MemoryDocumentStore()
    event = IngestEvent(
        "event-10", "doc-10", "report.pdf", "uploads/doc-10.pdf", datetime.now(timezone.utc)
    )
    settings = Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)})

    result = handle_ingest_event(
        event,
        lambda _event: DocumentSummary("报告标题", "报告摘要"),
        store,
        settings,
        sleep=lambda _seconds: None,
    )

    record = store.get("doc-10")
    assert result.status is DocumentStatus.COMPLETED
    assert record.title == "报告标题"
    assert record.summary == "报告摘要"
    assert record.parsed_at is not None
