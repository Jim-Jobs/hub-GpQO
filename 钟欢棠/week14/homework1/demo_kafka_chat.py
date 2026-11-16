"""作业 1-2：用 Kafka 传递消息（简易群聊）。

知识点：
  - Producer.produce + delivery callback（确认消息送达）；
  - Consumer.subscribe + poll + 手动 commit（消费组、偏移量）；
  - 消息 key 用于分区路由，JSON value 用于结构化消息；
  - 同一个 topic 可以被多个消费组独立消费。

用法（开两个终端，需要 Kafka 已启动）：
  终端 A: python homework1/demo_kafka_chat.py --role consumer --group chat-a
  终端 B: python homework1/demo_kafka_chat.py --role producer --user 张三
  单机自测: python homework1/demo_kafka_chat.py --role demo

环境变量：KAFKA_BOOTSTRAP（默认 localhost:9092）
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
import uuid
from datetime import datetime

from confluent_kafka import Consumer, KafkaException, Producer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
TOPIC = os.getenv("KAFKA_DEMO_TOPIC", "chat.messages")

_stop = threading.Event()


def build_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "client.id": "homework1-producer",
            "message.timeout.ms": 15000,
        }
    )


def build_consumer(group: str) -> Consumer:
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "client.id": f"homework1-{group}",
            "auto.offset.reset": "earliest",       # 首次启动从最早消息开始
            "enable.auto.commit": False,           # 处理成功后再提交偏移量
        }
    )
    consumer.subscribe([TOPIC])
    return consumer


def send(producer: Producer, user: str, text: str) -> None:
    envelope = {
        "id": uuid.uuid4().hex[:8],
        "user": user,
        "text": text,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    producer.produce(
        TOPIC,
        key=user.encode("utf-8"),
        value=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        callback=lambda err, msg: print(
            f"  [送达] partition={msg.partition()} offset={msg.offset()}" if not err else f"  [失败] {err}"
        ),
    )
    producer.poll(0)
    producer.flush(10)


def consume(group: str) -> None:
    consumer = build_consumer(group)
    print(f"[consumer] group={group} topic={TOPIC}，等待消息中（Ctrl+C 退出）...")
    try:
        while not _stop.is_set():
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"[consumer] Kafka 错误：{msg.error()}")
                continue
            try:
                envelope = json.loads(msg.value().decode("utf-8"))
            except (ValueError, AttributeError):
                print(f"[consumer] 忽略非 JSON 消息：{msg.value()!r}")
                consumer.commit(msg)
                continue
            key = msg.key().decode("utf-8") if msg.key() else "-"
            print(f"[consumer] [{key}] {envelope['user']}: {envelope['text']}  ({envelope['ts']})")
            consumer.commit(msg)  # 手动提交，保证「至少一次」处理语义
    except KafkaException as exc:
        print(f"[consumer] 消费异常：{exc}")
    finally:
        consumer.close()


def produce(user: str) -> None:
    producer = build_producer()
    print(f"[producer] user={user} topic={TOPIC}，输入消息后回车发送，输入 quit 退出")
    try:
        while not _stop.is_set():
            text = input("> ").strip()
            if not text:
                continue
            if text in {"quit", "exit", "q"}:
                break
            send(producer, user, text)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        producer.flush(10)


def run_demo() -> None:
    """单进程内同时启动生产者和消费者，快速验证 Kafka 通路。"""
    group = f"demo-{uuid.uuid4().hex[:8]}"
    thread = threading.Thread(target=consume, args=(group,), daemon=True)
    thread.start()
    time.sleep(2)  # 等待 consumer 加入消费组
    producer = build_producer()
    for text in ["你好，我是 Kafka 消息 1", "Redis 负责存历史，Kafka 负责传消息", "最后一条消息"]:
        print(f"[producer] 发送: {text}")
        send(producer, "演示用户", text)
        time.sleep(0.6)
    time.sleep(3)
    _stop.set()
    thread.join(timeout=3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kafka 消息传递示例")
    parser.add_argument("--role", choices=["producer", "consumer", "demo"], default="demo")
    parser.add_argument("--user", default="匿名", help="producer 的名字")
    parser.add_argument("--group", default="chat-group-1", help="consumer 消费组")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    print(f"Kafka bootstrap: {BOOTSTRAP}")
    try:
        if args.role == "producer":
            produce(args.user)
        elif args.role == "consumer":
            consume(args.group)
        else:
            run_demo()
    except KeyboardInterrupt:
        _stop.set()
