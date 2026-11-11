"""
用远程 Redis 存储历史对话（可直接运行的简短示例）

依赖：pip install redis    （--fake 模式另需：pip install fakeredis）
配置：二选一
  1) 环境变量 REDIS_URL=redis://:密码@主机:6379/0   （TLS 用 rediss://）
  2) 环境变量/命令行 REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB
用法：
  python redis_chat_history.py --host 1.2.3.4 --port 6379 --password xxx --session user_001
  python redis_chat_history.py --session user_001          # 全用环境变量
  python redis_chat_history.py --fake                      # 本机无 Redis 时用内存模拟
本机无 Redis 又想走真实 TCP：另开终端先起一个协议兼容服务（纯内存），
  python -c "import fakeredis; fakeredis.TcpFakeServer(('127.0.0.1',6379)).serve_forever()"
  再直接 python redis_chat_history.py（默认就是 127.0.0.1:6379）
交互命令：/history 查看历史  /clear 清空  /quit 退出
说明：启动先做 3 秒 TCP 预检（--timeout 可调），不可达时快速报错，不会长时间卡住。
"""

import argparse
import json
import os
from datetime import datetime

import redis

DEFAULT_TTL = 7 * 24 * 3600  # 每个会话的历史保留 7 天


def build_client(url=None, host="127.0.0.1", port=6379, password=None, db=0):
    """建立到远程 Redis 的连接；decode_responses 让取出的数据直接是 str"""
    kwargs = dict(
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    if url:
        return redis.Redis.from_url(url, **kwargs)
    return redis.Redis(host=host, port=port, password=password or None, db=db, **kwargs)


class ChatHistory:
    """一个会话 = 一个 Redis List，每条消息是一个 JSON 字符串"""

    def __init__(self, client, ttl=DEFAULT_TTL):
        self.r = client
        self.ttl = ttl

    @staticmethod
    def _key(session_id):
        return f"chat:history:{session_id}"

    def add(self, session_id, role, content):
        msg = {
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        key = self._key(session_id)
        pipe = self.r.pipeline()
        pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
        pipe.expire(key, self.ttl)  # 每次对话刷新过期时间
        pipe.execute()
        return msg

    def get(self, session_id, limit=20):
        """按时间正序取最近 limit 条"""
        return [json.loads(s) for s in self.r.lrange(self._key(session_id), -limit, -1)]

    def as_messages(self, session_id, limit=20):
        """转成大模型接口常用的 [{"role": ..., "content": ...}] 格式"""
        return [{"role": m["role"], "content": m["content"]} for m in self.get(session_id, limit)]

    def clear(self, session_id):
        return self.r.delete(self._key(session_id))

    def sessions(self):
        return [k.split(":", 2)[-1] for k in self.r.scan_iter(match="chat:history:*")]


def interactive(history, session_id):
    print(f"会话 [{session_id}] 已就绪，输入内容记录对话；/history 查看，/clear 清空，/quit 退出")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/history":
            for m in history.get(session_id):
                print(f"  [{m['ts']}] {m['role']}: {m['content']}")
            continue
        if text == "/clear":
            history.clear(session_id)
            print("  已清空")
            continue

        history.add(session_id, "user", text)
        # 接大模型时：把 history.as_messages(session_id) 作为 messages 传入，
        # 再把模型回复用 history.add(session_id, "assistant", reply) 存回去。
        reply = f"(echo) {text}"
        history.add(session_id, "assistant", reply)
        print(f"bot> {reply}")


def precheck(url, host, port, timeout=3):
    """TCP 预检：redis-py 在不可达主机上会长时间重试，先快速探一下"""
    import socket
    from urllib.parse import urlparse

    if url:
        u = urlparse(url)
        host, port = u.hostname or host, u.port or port
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
    except OSError as e:
        raise SystemExit(
            f"TCP 预检失败：{host}:{port} 不可达（{type(e).__name__}: {e}）\n"
            "排查：本机是否在 Redis 所在内网/VPN、地址端口是否正确、"
            "服务器 bind/protected-mode、防火墙或安全组是否放行"
        )
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description="用远程 Redis 存储历史对话")
    p.add_argument("--url", default=os.getenv("REDIS_URL"))
    p.add_argument("--host", default=os.getenv("REDIS_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("REDIS_PORT", 6379)))
    p.add_argument("--password", default=os.getenv("REDIS_PASSWORD"))
    p.add_argument("--db", type=int, default=int(os.getenv("REDIS_DB", 0)))
    p.add_argument("--session", default="demo", help="会话 ID，多用户时换成 user_id")
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="历史保留秒数，默认 7 天")
    p.add_argument("--fake", action="store_true", help="本机无 Redis 时用内存模拟，接口完全一致")
    p.add_argument("--timeout", type=int, default=3, help="TCP 预检超时秒数，默认 3")
    args = p.parse_args()

    if args.fake:
        import fakeredis  # 仅此模式需要：pip install fakeredis

        client = fakeredis.FakeRedis(decode_responses=True)
    else:
        precheck(args.url, args.host, args.port, args.timeout)
        client = build_client(args.url, args.host, args.port, args.password, args.db)

    try:
        client.ping()
    except redis.RedisError as e:
        raise SystemExit(
            f"连接 Redis 失败: {e}\n"
            "TCP 可达但握手失败，排查：密码/AUTH、TLS（rediss://）、db 编号、"
            "服务端 protected-mode（本机自测可加 --fake）"
        )

    if args.fake:
        print("已连接 Redis：内存模拟模式（进程内，退出后数据清空）")
    else:
        print(f"已连接 Redis：{args.url or f'redis://{args.host}:{args.port}/{args.db}'}")
    interactive(ChatHistory(client, args.ttl), args.session)


if __name__ == "__main__":
    main()
