"""作业 1-1：用 Redis 存储历史对话。

知识点：
  - List (RPUSH / LRANGE / LTRIM) 保存有序消息；
  - Hash (HSET / HGETALL) 保存会话元信息；
  - EXPIRE / TTL 控制会话过期；Pipeline 保证一次写入的原子性；
  - 对话内容按 JSON 序列化，可直接被大模型 messages 复用。

用法：
  python homework1/demo_redis_chat.py                 # 交互式聊天
  python homework1/demo_redis_chat.py --demo         # 跑一段脚本化演示
  python homework1/demo_redis_chat.py --session s1   # 指定会话
环境变量：REDIS_URL（默认 redis://localhost:6379/0）
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MAX_MESSAGES = 20          # 每个会话最多保留的消息条数
SESSION_TTL = 3600         # 会话过期时间（秒）


class RedisChatHistory:
    """基于 Redis 的多轮对话存储器。"""

    def __init__(self, session_id: str) -> None:
        self.client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.session_id = session_id
        self.messages_key = f"chat:history:{session_id}"
        self.meta_key = f"chat:meta:{session_id}"

    def add(self, role: str, content: str) -> None:
        message = {
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        pipe = self.client.pipeline()
        pipe.rpush(self.messages_key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(self.messages_key, -MAX_MESSAGES, -1)
        pipe.expire(self.messages_key, SESSION_TTL)
        pipe.hset(
            self.meta_key,
            mapping={"session_id": self.session_id, "updated_at": message["ts"]},
        )
        pipe.hincrby(self.meta_key, "turns", 1)
        pipe.expire(self.meta_key, SESSION_TTL)
        pipe.execute()

    def all(self) -> list[dict]:
        raw = self.client.lrange(self.messages_key, 0, -1)
        return [json.loads(item) for item in raw]

    def as_llm_messages(self, limit: int = 10) -> list[dict]:
        """直接转换成 OpenAI 兼容的 messages 格式。"""
        return [{"role": m["role"], "content": m["content"]} for m in self.all()[-limit:]]

    def clear(self) -> None:
        self.client.delete(self.messages_key, self.meta_key)

    def info(self) -> dict:
        return {
            "messages_key": self.messages_key,
            "meta_key": self.meta_key,
            "count": self.client.llen(self.messages_key),
            "ttl": self.client.ttl(self.messages_key),
            "meta": self.client.hgetall(self.meta_key),
        }


def mock_assistant_reply(question: str, history: RedisChatHistory) -> str:
    """不依赖大模型的模拟回复，用来验证上下文是否被正确保存。"""
    count = len(history.all())
    return f"（模拟回复）已收到第 {count} 条消息：{question[:60]}"


def interactive(session_id: str) -> None:
    history = RedisChatHistory(session_id)
    print(f"Redis: {REDIS_URL}  会话: {session_id}")
    print("命令：history 查看历史 / stats 查看键与 TTL / clear 清空 / quit 退出\n")
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not text:
            continue
        if text in {"quit", "exit", "q"}:
            break
        if text == "history":
            for m in history.all():
                print(f"  [{m['ts']}] {m['role']}: {m['content']}")
            continue
        if text == "stats":
            print(" ", json.dumps(history.info(), ensure_ascii=False, indent=2))
            continue
        if text == "clear":
            history.clear()
            print("  会话已清空")
            continue
        history.add("user", text)
        reply = mock_assistant_reply(text, history)
        history.add("assistant", reply)
        print(f"助手> {reply}")
    print("最终状态:", json.dumps(history.info(), ensure_ascii=False))


def run_demo(session_id: str) -> None:
    history = RedisChatHistory(session_id)
    history.clear()
    for text in ["你好，帮我记一下 Redis 的用法", "Kafka 是做什么的？", "把前面两点总结一下"]:
        history.add("user", text)
        reply = mock_assistant_reply(text, history)
        history.add("assistant", reply)
        print(f"你> {text}\n助手> {reply}")
    print("\nRedis 中的历史对话：")
    for m in history.all():
        print(f"  {m['role']}: {m['content']}")
    print("\n送入 LLM 的 messages：", json.dumps(history.as_llm_messages(), ensure_ascii=False))
    print("键信息：", json.dumps(history.info(), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redis 历史对话存储示例")
    parser.add_argument("--session", default="demo-session", help="会话 ID")
    parser.add_argument("--demo", action="store_true", help="非交互式演示")
    args = parser.parse_args()
    try:
        if args.demo:
            run_demo(args.session)
        else:
            interactive(args.session)
    except redis.exceptions.ConnectionError as exc:
        raise SystemExit(f"无法连接 Redis（{REDIS_URL}）：{exc}\n请先启动 Redis：docker compose up -d redis") from exc
