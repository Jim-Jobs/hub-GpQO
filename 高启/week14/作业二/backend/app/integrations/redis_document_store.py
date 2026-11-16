"""Redis 文档状态与资料库概览存储适配器。"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

from backend.app.documents.domain.models import CorpusProfile, DocumentRecord, DocumentStatus
from backend.app.documents.indexing.corpus_profile import update_corpus_profile
from backend.app.integrations.errors import DocumentStoreError


class RedisDocumentStore:
    """将文档状态和 completed 文档生成的资料库概览存入 Redis。"""

    def __init__(self, client: Redis, key_prefix: str = "rag:document:") -> None:
        """接收共享 Redis 客户端；消费者线程共享该实例完成状态和概览更新。"""
        self._client = client
        self._key_prefix = key_prefix
        self._profile_key = "rag:corpus:profile"
        self._completion_lock = RLock()

    def save(self, record: DocumentRecord) -> None:
        """按 document_id 保存或覆盖状态记录。"""
        try:
            self._client.set(self._key(record.document_id), record.to_json())
        except Exception as error:
            raise DocumentStoreError("Redis 文档状态写入失败") from error

    def get(self, document_id: str) -> DocumentRecord | None:
        """读取并反序列化文档状态；key 不存在时返回 None。"""
        try:
            payload = self._client.get(self._key(document_id))
        except Exception as error:
            raise DocumentStoreError("Redis 文档状态读取失败") from error
        if payload is None:
            return None
        try:
            # Redis 可返回字节串或字符串；解码与模型还原属于同一格式边界。
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            if not isinstance(payload, str):
                raise TypeError("Redis 文档状态必须是字符串或字节串")
            return DocumentRecord.from_json(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise DocumentStoreError("Redis 文档状态格式无效") from error

    def complete(self, record: DocumentRecord) -> None:
        """事务式保存 completed 状态和仅含已完成文档的资料库概览。"""
        if record.status is not DocumentStatus.COMPLETED:
            raise ValueError("complete 只接受 completed 状态记录")
        try:
            with self._completion_lock:
                records = self._completed_records()
                records = [item for item in records if item.document_id != record.document_id]
                profile = update_corpus_profile([*records, record])
                # Redis MULTI/EXEC 让状态和概览在同一次事务中可见，避免一方先更新。
                with self._client.pipeline(transaction=True) as pipeline:
                    pipeline.set(self._key(record.document_id), record.to_json())
                    pipeline.set(self._profile_key, profile.to_json())
                    pipeline.execute()
        except Exception as error:
            raise DocumentStoreError("Redis 文档完成状态和资料库概览写入失败") from error

    def get_corpus_profile(self) -> CorpusProfile | None:
        """读取资料库主题概览；尚无完成文档时返回 None。"""
        try:
            payload = self._client.get(self._profile_key)
        except Exception as error:
            raise DocumentStoreError("Redis 资料库概览读取失败") from error
        if payload is None:
            return None
        try:
            return CorpusProfile.from_json(_decode_payload(payload))
        except (KeyError, TypeError, ValueError) as error:
            raise DocumentStoreError("Redis 资料库概览格式无效") from error

    def list_completed_documents(self) -> list[DocumentRecord]:
        """读取已完成文档列表，用于问答检索过滤 Milvus 命中范围。"""
        try:
            with self._completion_lock:
                return self._completed_records()
        except Exception as error:
            raise DocumentStoreError("Redis 已完成文档列表读取失败") from error

    def _completed_records(self) -> list[DocumentRecord]:
        """扫描并读取已完成状态；调用方持有完成锁以串行化本进程更新。"""
        # ponytail: 全量扫描易于保证重建结果完整；规模增大后改用 Redis completed 索引。
        records: list[DocumentRecord] = []
        for key in self._client.scan_iter(match=f"{self._key_prefix}*"):
            payload = self._client.get(key)
            if payload is None:
                continue
            record = DocumentRecord.from_json(_decode_payload(payload))
            if record.status is DocumentStatus.COMPLETED:
                records.append(record)
        return records

    def _key(self, document_id: str) -> str:
        """生成文档状态专用 key。"""
        return f"{self._key_prefix}{document_id}"


def _decode_payload(payload: str | bytes) -> str:
    """把 Redis UTF-8 字节串或字符串统一为 JSON 文本。"""
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if not isinstance(payload, str):
        raise TypeError("Redis 数据必须是字符串或字节串")
    return payload
