"""Elasticsearch 异步客户端封装。

对应文档：``docs/01-架构设计.md`` §2.2（es-client）、``docs/03-Kafka设计.md`` §5.1（幂等）。

幂等约定（重放同一份 PDF 不能产生重复数据）：

* 文档 / 块 / 图片都用**固定 ``_id``** 写入（``index`` 操作而非 ``create``），重复写覆盖而非报错；
* ``chunk_id = {doc_id}_c{seq:04d}``、``image_id = {doc_id}_p{page}_i{idx}``，序号由内容顺序决定，
  同内容必得同 ID；
* 重解析前先调用 :meth:`ESClient.delete_document_content` 清掉旧块旧图。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import NotFoundError

from app.config import Settings
from app.storage.index_schema import (
    INDEX_CHUNKS,
    INDEX_DOCUMENTS,
    INDEX_IMAGES,
    INDEX_SESSIONS,
    build_index_definitions,
)

logger = logging.getLogger(__name__)


class ESError(RuntimeError):
    """ES 操作失败。消息需对用户可读。"""


def build_bulk_operations(
    index: str, items: Sequence[tuple[str, Mapping[str, Any]]]
) -> list[dict[str, Any]]:
    """把 ``(id, source)`` 列表转成 elasticsearch-py 的 bulk operations 结构。

    抽成纯函数是为了能脱离 ES 单测写入顺序（action 与 source 必须严格交替）。
    """
    operations: list[dict[str, Any]] = []
    for doc_id, source in items:
        operations.append({"index": {"_index": index, "_id": doc_id}})
        operations.append(dict(source))
    return operations


class ESClient:
    """ES 访问的统一入口。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncElasticsearch(
            hosts=[settings.es_url],
            request_timeout=settings.es_request_timeout,
            retry_on_timeout=True,
            max_retries=3,
        )

    @property
    def raw(self) -> AsyncElasticsearch:
        """底层客户端，仅供检索等需要原生 query DSL 的场景使用。"""
        return self._client

    # ---------- 索引管理 ----------

    async def ensure_indices(self) -> list[str]:
        """创建缺失的索引，返回本次新建的索引名列表。已存在则跳过。"""
        definitions = build_index_definitions(self._settings.es_vector_dim)
        created: list[str] = []
        for name, body in definitions.items():
            if await self._client.indices.exists(index=name):
                continue
            await self._client.indices.create(index=name, **body)
            created.append(name)
            logger.info("已创建 ES 索引 %s", name)
        return created

    # ---------- 写入 ----------

    async def index_document(
        self, index: str, doc_id: str, source: Mapping[str, Any]
    ) -> None:
        """幂等写入单条文档（固定 ``_id``，重复写覆盖）。"""
        await self._client.index(index=index, id=doc_id, document=dict(source))

    async def bulk_index(
        self, index: str, items: Sequence[tuple[str, Mapping[str, Any]]]
    ) -> int:
        """批量幂等写入，返回成功条数。任一条失败整体抛错。"""
        if not items:
            return 0
        operations = build_bulk_operations(index, items)
        response = await self._client.bulk(operations=operations, refresh=False)
        if response.get("errors"):
            failures = [
                item
                for item in response.get("items", [])
                if item.get("index", {}).get("error") is not None
            ]
            raise ESError(f"批量写入 {index} 失败 {len(failures)} 条：{failures[:3]}")
        return len(items)

    # ---------- 读取 / 删除 ----------

    async def get(self, index: str, doc_id: str) -> dict[str, Any] | None:
        """按 ``_id`` 取文档，不存在返回 ``None``（404 容错，不抛异常）。"""
        try:
            response = await self._client.get(index=index, id=doc_id)
        except NotFoundError:
            return None
        return response["_source"]

    async def exists(self, index: str, doc_id: str) -> bool:
        return await self._client.exists(index=index, id=doc_id)

    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        """原生查询 DSL。索引为空/不存在时返回空结果而不是抛错。"""
        try:
            return await self._client.search(index=index, **body)
        except NotFoundError:
            return {"hits": {"total": {"value": 0}, "hits": []}}

    async def list_documents(self, size: int = 200) -> list[dict[str, Any]]:
        """文档列表，按 ``created_at`` 倒序（``docs/04`` §2.2）。"""
        result = await self.search(
            INDEX_DOCUMENTS,
            {
                "query": {"match_all": {}},
                "size": size,
                "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}],
            },
        )
        return [{"doc_id": hit["_id"], **hit["_source"]} for hit in result["hits"]["hits"]]

    async def delete(self, index: str, doc_id: str, *, refresh: bool = False) -> bool:
        """删除单条，返回是否真的删掉了（原本就不存在返回 False）。

        ``refresh=True`` 让删除立刻对搜索可见（ES 默认近实时，有约 1 秒延迟）。
        前端删完马上刷新列表时，不加这个会看到"删了还在"。
        """
        try:
            await self._client.delete(index=index, id=doc_id, refresh=refresh)
        except NotFoundError:
            return False
        return True

    async def delete_document_content(self, doc_id: str) -> None:
        """删除某文档的全部块与图片。

        重解析前必须调用 —— 这是"序号做 ID"方案能保持幂等的前提。
        """
        for index in (INDEX_CHUNKS, INDEX_IMAGES):
            await self._client.delete_by_query(
                index=index,
                query={"term": {"doc_id": doc_id}},
                refresh=True,
                conflicts="proceed",
            )
            logger.info("已清理 %s 中 doc_id=%s 的旧数据", index, doc_id)

    async def delete_document(self, doc_id: str) -> bool:
        """删除文档及其全部块与图片，返回文档本身是否存在。"""
        existed = await self.delete(INDEX_DOCUMENTS, doc_id, refresh=True)
        await self.delete_document_content(doc_id)
        return existed

    # ---------- 运维 ----------

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
            logger.warning("ES ping 失败", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.close()


__all__ = [
    "ESClient",
    "ESError",
    "build_bulk_operations",
    "INDEX_CHUNKS",
    "INDEX_DOCUMENTS",
    "INDEX_IMAGES",
    "INDEX_SESSIONS",
]
