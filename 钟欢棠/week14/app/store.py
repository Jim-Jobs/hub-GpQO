"""Redis 状态存储：文档状态、问答结果、会话历史、事件流。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

try:
    import redis
except ImportError:  # 本地预览模式可不安装 redis 包
    redis = None

from .config import settings

DOC_SET = "mmrag:docs"
EVENT_LIST = "mmrag:events"
HISTORY_MAX = 40
EVENT_MAX = 100


def _s(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


class _LocalPipeline:
    """模拟 redis pipeline：攒操作后一次性执行。"""

    def __init__(self, client: "LocalRedis") -> None:
        self.client = client
        self.operations: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def enqueue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return enqueue

    def execute(self):
        return [self.client._call(name, args, kwargs) for name, args, kwargs in self.operations]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class LocalRedis:
    """无 Redis 服务时的文件版替代，只实现本项目用到的命令子集。

    数据落在 data/local_state.json，API 与 Worker 在同一进程内共享，重启后状态仍在。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {"hashes": {}, "sets": {}, "lists": {}}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                for key in self._data:
                    if isinstance(loaded.get(key), dict):
                        self._data[key] = loaded[key]
            except (ValueError, OSError):
                pass

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _call(self, name: str, args: tuple, kwargs: dict):
        with self._lock:
            handler = getattr(self, "_cmd_" + name, None)
            if handler is None:
                raise NotImplementedError(f"LocalRedis 不支持命令：{name}")
            result = handler(*args, **kwargs)
            self._save()
            return result

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def caller(*args, **kwargs):
            return self._call(name, args, kwargs)

        return caller

    def pipeline(self) -> _LocalPipeline:
        return _LocalPipeline(self)

    # ---- 命令子集 ----
    def _cmd_ping(self):
        return True

    def _cmd_hset(self, key, mapping=None, field=None, value=None):
        item = self._data["hashes"].setdefault(key, {})
        if mapping:
            item.update({str(k): str(v) for k, v in mapping.items()})
        if field is not None:
            item[str(field)] = str(value)
        return len(item)

    def _cmd_hgetall(self, key):
        return dict(self._data["hashes"].get(key, {}))

    def _cmd_hincrby(self, key, field, amount=1):
        item = self._data["hashes"].setdefault(key, {})
        value = int(item.get(str(field), "0")) + int(amount)
        item[str(field)] = str(value)
        return value

    def _cmd_sadd(self, key, *values):
        item = self._data["sets"].setdefault(key, [])
        for value in values:
            if value not in item:
                item.append(value)
        return len(values)

    def _cmd_smembers(self, key):
        return set(self._data["sets"].get(key, []))

    def _cmd_expire(self, key, seconds):
        return True

    def _cmd_ttl(self, key):
        return -1

    def _cmd_rpush(self, key, *values):
        item = self._data["lists"].setdefault(key, [])
        item.extend(str(v) for v in values)
        return len(item)

    def _cmd_lpush(self, key, *values):
        item = self._data["lists"].setdefault(key, [])
        for value in values:
            item.insert(0, str(value))
        return len(item)

    def _cmd_lrange(self, key, start, end):
        item = self._data["lists"].get(key, [])
        begin = max(0, len(item) + start) if start < 0 else start
        stop = len(item) if end < 0 else min(end + 1, len(item))
        return item[begin:stop]

    def _cmd_ltrim(self, key, start, end):
        item = self._data["lists"].get(key, [])
        begin = max(0, len(item) + start) if start < 0 else min(start, len(item))
        stop = len(item) if end < 0 else min(end + 1, len(item))
        self._data["lists"][key] = item[begin:stop]
        return True

    def _cmd_llen(self, key):
        return len(self._data["lists"].get(key, []))

    def _cmd_delete(self, *keys):
        for key in keys:
            for group in self._data.values():
                group.pop(key, None)
        return len(keys)


class Store:
    def __init__(self, url: str | None = None) -> None:
        if settings.local_mode:
            self.client = LocalRedis(settings.data_dir / "local_state.json")
        else:
            if redis is None:
                raise RuntimeError("缺少 redis 依赖，请执行：pip install -r requirements.txt")
            self.client = redis.Redis.from_url(url or settings.redis_url, decode_responses=True)

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:  # noqa: BLE001 - Redis 不可用 / 本地存储异常都算不健康
            return False

    # ---------- 文档 ----------
    def create_doc(self, doc_id: str, filename: str, size: int, status: str) -> dict:
        doc = {
            "id": doc_id,
            "filename": filename,
            "size": str(size),
            "status": status,
            "created_at": str(time.time()),
            "chunk_count": "0",
            "image_count": "0",
            "pages": "0",
            "parser": "",
            "error": "",
        }
        pipe = self.client.pipeline()
        pipe.hset(f"mmrag:doc:{doc_id}", mapping=doc)
        pipe.sadd(DOC_SET, doc_id)
        pipe.execute()
        return doc

    def update_doc(self, doc_id: str, **fields) -> None:
        if fields:
            self.client.hset(f"mmrag:doc:{doc_id}", mapping={k: _s(v) for k, v in fields.items()})

    def get_doc(self, doc_id: str) -> dict | None:
        doc = self.client.hgetall(f"mmrag:doc:{doc_id}")
        return doc or None

    def list_docs(self) -> list[dict]:
        docs = [self.get_doc(doc_id) for doc_id in self.client.smembers(DOC_SET)]
        docs = [d for d in docs if d]
        return sorted(docs, key=lambda d: float(d.get("created_at", 0)), reverse=True)

    # ---------- 问答任务 ----------
    def create_qa(self, request_id: str, session_id: str, question: str) -> dict:
        state = {
            "id": request_id,
            "session_id": session_id,
            "question": question,
            "status": "排队中",
            "answer": "",
            "sources": "[]",
            "images": "[]",
            "mode": "",
            "created_at": str(time.time()),
            "error": "",
        }
        self.client.hset(f"mmrag:qa:{request_id}", mapping=state)
        self.client.expire(f"mmrag:qa:{request_id}", settings.qa_ttl)
        return state

    def update_qa(self, request_id: str, **fields) -> None:
        if fields:
            self.client.hset(f"mmrag:qa:{request_id}", mapping={k: _s(v) for k, v in fields.items()})
            self.client.expire(f"mmrag:qa:{request_id}", settings.qa_ttl)

    def get_qa(self, request_id: str) -> dict | None:
        state = self.client.hgetall(f"mmrag:qa:{request_id}")
        if not state:
            return None
        for key in ("sources", "images"):
            try:
                state[key] = json.loads(state.get(key, "[]"))
            except ValueError:
                state[key] = []
        return state

    def apply_answer(self, payload: dict) -> None:
        """问答 Worker 通过 Kafka 送回答案后，落库到问答状态 + 会话历史。"""
        request_id = payload.get("request_id", "")
        if not request_id:
            return
        answer = str(payload.get("answer", ""))
        status = "完成" if payload.get("status") == "done" else "失败"
        self.update_qa(
            request_id,
            status=status,
            answer=answer,
            sources=payload.get("sources", []),
            images=payload.get("images", []),
            mode=payload.get("mode", ""),
            error="" if status == "完成" else payload.get("error", ""),
            finished_at=time.time(),
        )
        if answer:
            self.append_history(
                payload.get("session_id", "default"),
                "assistant",
                answer,
                payload.get("images", []),
            )

    # ---------- 会话历史（作业 1 的 Redis 能力在这里复用）----------
    def append_history(self, session_id: str, role: str, content: str, images: list[str] | None = None) -> None:
        key = f"mmrag:history:{session_id}"
        message = {"role": role, "content": content, "images": images or [], "ts": time.time()}
        pipe = self.client.pipeline()
        pipe.rpush(key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(key, -HISTORY_MAX, -1)
        pipe.expire(key, settings.session_ttl)
        pipe.execute()

    def get_history(self, session_id: str) -> list[dict]:
        raw = self.client.lrange(f"mmrag:history:{session_id}", 0, -1)
        result = []
        for item in raw:
            try:
                result.append(json.loads(item))
            except ValueError:
                continue
        return result

    def clear_history(self, session_id: str) -> None:
        self.client.delete(f"mmrag:history:{session_id}")

    # ---------- Kafka 事件流（供前端展示）----------
    def push_event(self, event: dict) -> None:
        pipe = self.client.pipeline()
        pipe.lpush(EVENT_LIST, json.dumps(event, ensure_ascii=False))
        pipe.ltrim(EVENT_LIST, 0, EVENT_MAX - 1)
        pipe.execute()

    def list_events(self, limit: int = 30) -> list[dict]:
        result = []
        for item in self.client.lrange(EVENT_LIST, 0, max(0, limit - 1)):
            try:
                result.append(json.loads(item))
            except ValueError:
                continue
        return result


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = Store()
    return _store
