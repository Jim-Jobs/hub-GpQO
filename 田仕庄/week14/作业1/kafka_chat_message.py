"""
通过 Kafka 传递消息（简短示例）

依赖：Python 3.8+，pip install confluent-kafka
配置：见本文件顶部的 DEFAULT_BROKER / DEFAULT_TOPIC / DEFAULT_GROUP，改那一处即可
      优先级：命令行参数 > 环境变量 KAFKA_BOOTSTRAP/KAFKA_TOPIC/KAFKA_GROUP > 顶部常量
      当前默认值：10.42.35.157:9092 / test-topic-tsz1111 / chat-demo

在远程服务器（Kafka 所在机器）上手动运行：
  python3 kafka_chat_message.py check
  python3 kafka_chat_message.py send "你好" --session user_001
  python3 kafka_chat_message.py listen --count 20 --offset earliest --group probe-1
  # 若 broker 只监听 127.0.0.1，上述每条命令加 --broker localhost:9092
  # 若 check 提示 topic 不存在：先 send 一次让 broker 自动创建，或用 kafka-topics.sh 建
  python3 kafka_chat_message.py demo       # 无 broker 时的内存演示，不连 Kafka

消息格式：{"session", "role", "content", "ts"}，与 redis_chat_history.py 的字段兼容，
          消费后可用 ChatHistory.add(m["session"], m["role"], m["content"]) 直接落库。
"""

import argparse
import json
import os
import queue
from datetime import datetime

from confluent_kafka import Consumer, Producer

# ===== 连接配置：部署到服务器时改这一处即可 =====
DEFAULT_BROKER = "10.42.35.157:9092"
DEFAULT_TOPIC = "test-topic-tsz1111"
DEFAULT_GROUP = "chat-demo"
# ==============================================


def make_message(session, role, content):
    return {
        "session": session,
        "role": role,
        "content": content,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def send(producer, topic, session, text):
    """发一条消息；同一 session 用同一个 key，保证分区内有序"""
    msg = make_message(session, "user", text)
    errors = []
    producer.produce(
        topic,
        key=session.encode("utf-8"),
        value=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
        callback=lambda err, _: errors.append(err) if err else None,
    )
    producer.poll(0)
    remaining = producer.flush(10)
    if errors:
        raise RuntimeError(f"投递失败：{errors[0]}")
    if remaining:
        raise RuntimeError(f"还有 {remaining} 条消息未被确认（broker 不可达或超时）")
    return msg


def listen(consumer, limit=None, timeout=1.0, broker_hint=""):
    """收消息并打印；limit=收到多少条后退出，None=持续（Ctrl+C 结束）"""
    n, idle, warned = 0, 0, False
    while limit is None or n < limit:
        m = consumer.poll(timeout)
        if m is None:
            idle += 1
            if not warned and idle >= 5:
                print(f"  暂未收到消息，请检查 broker 是否可达：{broker_hint}", flush=True)
                warned = True
            if limit is not None and idle >= 10:
                break
            continue
        if m.error():
            print("  消费错误：", m.error())
            continue
        data = json.loads(m.value().decode("utf-8"))
        print(f"  [{data['ts']}] {data['session']}/{data['role']}: {data['content']}", flush=True)
        n += 1
    return n


class MemoryBus:
    """demo 模式用的极简内存消息总线，只模拟 produce/poll 接口，不经过 Kafka 协议"""

    class _Msg:
        def __init__(self, value, key):
            self._value, self._key = value, key

        def value(self):
            return self._value

        def key(self):
            return self._key

        def error(self):
            return None

    def __init__(self):
        self.q = queue.Queue()

    def produce(self, topic, key=None, value=None, callback=None):
        self.q.put(self._Msg(value, key))
        if callback:
            callback(None, None)

    def poll(self, timeout=1.0):
        if timeout == 0:  # 对应 Producer.poll(0)：只触发回调，不消费消息
            return None
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self, timeout=0):
        return 0


def check(broker, topic):
    """只读自检：broker 是否可达、topic 是否存在"""
    from confluent_kafka import KafkaException

    p = Producer({
        "bootstrap.servers": broker,
        "client.id": "chat-check",
        "socket.timeout.ms": 5000,
        "message.timeout.ms": 5000,
    })
    try:
        md = p.list_topics(timeout=6)
    except KafkaException as e:
        raise SystemExit(
            f"无法连接 broker {broker}：{e}\n"
            "排查：本机是否在 Kafka 所在内网/VPN、地址端口是否正确、防火墙或安全组是否放行"
        )
    print(f"broker {broker} 可达，共 {len(md.topics)} 个 topic")
    if topic in md.topics:
        print(f"topic {topic} 存在，分区数：{len(md.topics[topic].partitions)}")
        return True
    print(f"topic {topic} 不存在；现有 topic 示例：{list(md.topics)[:10]}")
    return False


def main():
    p = argparse.ArgumentParser(description="通过 Kafka 传递消息")
    p.add_argument("mode", choices=["check", "send", "listen", "demo"],
                   help="check 自检 / send 发一条 / listen 收消息 / demo 内存演示")
    p.add_argument("text", nargs="?", help="send 模式的消息内容")
    p.add_argument("--broker", default=os.getenv("KAFKA_BOOTSTRAP", DEFAULT_BROKER))
    p.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", DEFAULT_TOPIC))
    p.add_argument("--group", default=os.getenv("KAFKA_GROUP", DEFAULT_GROUP))
    p.add_argument("--session", default="demo")
    p.add_argument("--count", type=int, default=0, help="listen 收满 N 条后退出，0=持续")
    p.add_argument("--offset", choices=["earliest", "latest"], default="earliest",
                   help="消费起点，默认 earliest")
    args = p.parse_args()

    if args.mode != "demo":
        print(f"生效配置：broker={args.broker}  topic={args.topic}  group={args.group}", flush=True)

    if args.mode == "check":
        raise SystemExit(0 if check(args.broker, args.topic) else 2)

    if args.mode == "demo":
        print("内存总线演示（不连接 Kafka broker）：")
        bus = MemoryBus()
        for text in ("你好，Kafka", "第二条消息"):
            send(bus, args.topic, args.session, text)
        listen(bus, limit=2)
        return

    if args.mode == "send":
        if not args.text:
            p.error('send 模式需要提供消息文本，例如：send "你好"')
        producer = Producer({
            "bootstrap.servers": args.broker,
            "client.id": "chat-producer",
            "message.timeout.ms": 10000,
        })
        try:
            msg = send(producer, args.topic, args.session, args.text)
        except RuntimeError as e:
            raise SystemExit(
                f"发送失败（{args.broker} / {args.topic}）：{e}\n"
                "排查：先运行 check 模式；确认本机在 Kafka 所在内网/VPN、"
                "地址端口、防火墙或安全组是否放行；本机无 broker 时可用 demo 模式"
            )
        print("已发送：", json.dumps(msg, ensure_ascii=False))
        return

    consumer = Consumer({
        "bootstrap.servers": args.broker,
        "group.id": args.group,
        "auto.offset.reset": args.offset,
    })
    consumer.subscribe([args.topic])
    print(f"监听 topic={args.topic} @ {args.broker}，Ctrl+C 退出")
    try:
        listen(consumer, limit=args.count or None, broker_hint=args.broker)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
