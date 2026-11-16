"""将问答状态与最终响应以带 TTL 的 JSON 字符串保存到 Redis。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import ChatStatusRecord
from backend.app.integrations.errors import ChatStatusStoreError


class RedisChatStatusStore:
    """以独立 key 保存可轮询状态，绝不持久化请求正文或图片。"""

    def __init__(
        self, client: Any, key_prefix: str = "rag:chat:", ttl_seconds: int = 604_800
    ) -> None:
        """接收共享 Redis 客户端、key 前缀和状态保留秒数。"""
        if ttl_seconds < 1:
            raise ValueError("问答状态 TTL 必须是正整数")
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def save(self, record: ChatStatusRecord) -> None:
        """按 chat_id 保存状态 JSON，并为每次更新设置完整 TTL。"""
        try:
            self._client.set(
                self._key(record.chat_id), record.to_json(), ex=self._ttl_seconds
            )
        except Exception as error:
            raise ChatStatusStoreError("Redis 问答状态写入失败") from error

    def get(self, chat_id: str) -> ChatStatusRecord | None:
        """读取状态并验证 UTF-8、JSON 字段及枚举值。"""
        try:
            payload = self._client.get(self._key(chat_id))
        except Exception as error:
            raise ChatStatusStoreError("Redis 问答状态读取失败") from error
        if payload is None:
            return None
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            if not isinstance(payload, str):
                raise TypeError("问答状态必须是字符串或字节串")
            return ChatStatusRecord.from_json(payload)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
            raise ChatStatusStoreError("Redis 问答状态格式无效") from error

    def fail_interrupted(self) -> int:
        """扫描本服务状态 key，将进程重启前未结束的任务改为失败。"""
        failed_count = 0
        try:
            for key in self._client.scan_iter(match=f"{self._key_prefix}*"):
                chat_id = _chat_id_from_key(key, self._key_prefix)
                record = self.get(chat_id)
                if record is None or record.status not in {
                    ChatStatus.ACCEPTED,
                    ChatStatus.RUNNING,
                }:
                    continue
                failed = replace(
                    record,
                    status=ChatStatus.FAILED,
                    error="问答服务重启导致任务中断，请重新提交。",
                    updated_at=datetime.now(timezone.utc),
                )
                self.save(failed)
                failed_count += 1
        except ChatStatusStoreError:
            raise
        except Exception as error:
            raise ChatStatusStoreError("Redis 问答中断状态恢复失败") from error
        return failed_count

    def _key(self, chat_id: str) -> str:
        """生成问答状态专用 Redis key。"""
        return f"{self._key_prefix}{chat_id}"


def _chat_id_from_key(key: str | bytes, prefix: str) -> str:
    """从 Redis 扫描 key 中提取 chat_id，并拒绝不匹配的键。"""
    if isinstance(key, bytes):
        key = key.decode("utf-8")
    if not isinstance(key, str) or not key.startswith(prefix):
        raise ValueError("Redis 扫描返回了非问答状态 key")
    return key[len(prefix) :]
