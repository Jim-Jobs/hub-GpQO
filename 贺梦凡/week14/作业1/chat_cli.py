"""读取 Redis 里的历史，把用户消息发到 chat.events，并等待 chat.replies 通知。

这个程序不写历史。历史由 worker 在消费 Kafka 之后写入。
"""

import argparse
import json
import os
import queue
import signal
import threading
import time
import uuid

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from chat_common import (
    KAFKA_BOOTSTRAP,
    TOPIC_EVENTS,
    TOPIC_REPLIES,
    connect_redis,
    dumps,
    format_history,
    load_history,
    loads,
    new_message,
)

_stop = threading.Event()


def wait_for_kafka(max_attempts: int = 30) -> None:
    probe = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            probe.list_topics(timeout=5)
            probe.flush(1)
            return
        except KafkaException as exc:
            last_error = exc
            print(f"等待 Kafka ({attempt}/{max_attempts}): {exc}", flush=True)
            time.sleep(2)
    raise SystemExit(f"无法连接 Kafka {KAFKA_BOOTSTRAP}: {last_error}")


def make_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
            "enable.idempotence": True,
            "max.in.flight.requests.per.connection": 5,
        }
    )


def publish_user_message(producer: Producer, message: dict) -> None:
    delivered: dict = {}

    def on_delivery(err, _msg) -> None:
        if err is not None:
            delivered["err"] = err
        else:
            delivered["ok"] = True

    producer.produce(
        TOPIC_EVENTS,
        key=message["conversation_id"].encode(),
        value=dumps(message).encode(),
        on_delivery=on_delivery,
    )
    remaining = producer.flush(10)
    if remaining:
        raise RuntimeError(f"仍有 {remaining} 条消息未发出")
    if delivered.get("err") is not None:
        raise KafkaException(delivered["err"])
    if not delivered.get("ok"):
        raise RuntimeError("消息已提交，但没有收到 Kafka 回执")


def listen_replies(conversation_id: str, found: queue.Queue, ready: threading.Event) -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": f"chat-cli-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )

    def on_assign(_consumer, partitions) -> None:
        if not partitions:
            return
        print(f"已订阅 {TOPIC_REPLIES}: {len(partitions)} 个分区", flush=True)
        ready.set()

    consumer.subscribe([TOPIC_REPLIES], on_assign=on_assign)
    while not _stop.is_set():
        record = consumer.poll(0.5)
        if record is None:
            continue
        if record.error():
            if record.error().code() != KafkaError._PARTITION_EOF:
                print(f"读取回复失败: {record.error()}", flush=True)
            continue
        raw = record.value()
        if raw is None:
            continue
        try:
            message = loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(message, dict):
            continue
        if message.get("conversation_id") == conversation_id:
            found.put(message)
    consumer.close()


def print_history(client, conversation_id: str) -> None:
    print(format_history(load_history(client, conversation_id)), flush=True)


def send_and_wait(client, producer, conversation_id: str, content: str, found: queue.Queue, timeout: float) -> None:
    message = new_message(conversation_id, "user", content)
    publish_user_message(producer, message)
    print(f"已发到 {TOPIC_EVENTS}，msg_id={message['msg_id']}", flush=True)

    deadline = time.time() + timeout
    while time.time() < deadline and not _stop.is_set():
        remaining = deadline - time.time()
        try:
            reply = found.get(timeout=min(0.5, max(remaining, 0.1)))
        except queue.Empty:
            continue
        if reply.get("reply_to") == message["msg_id"]:
            print("回复已写入 Redis:", flush=True)
            print_history(client, conversation_id)
            return
    print("worker 还没有写回这条回复。消息仍在 Kafka 中，稍后输入 /history 查看。", flush=True)


def interactive(client, producer, conversation_id: str, found: queue.Queue, timeout: float) -> None:
    print(f"会话 {conversation_id}", flush=True)
    print("输入文字发送，/history 查看历史，/quit 退出", flush=True)
    print_history(client, conversation_id)
    while not _stop.is_set():
        try:
            line = input("> ").strip()
        except EOFError:
            print(flush=True)
            return
        if line in {"/quit", "quit", "exit"}:
            return
        if line in {"", "/history"}:
            print_history(client, conversation_id)
            continue
        send_and_wait(client, producer, conversation_id, line, found, timeout)


def require_content(content: str) -> str:
    text = content.strip()
    if not text:
        raise SystemExit("消息不能为空")
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向 Kafka 发送一条对话，并从 Redis 读取历史")
    parser.add_argument(
        "--conversation",
        default=os.environ.get("CONVERSATION_ID", "c1"),
        help="会话 id，同时作为 Kafka 消息 key",
    )
    parser.add_argument("--once", help="发送这一句后等待回复并退出")
    parser.add_argument("--timeout", type=float, default=20, help="等待回复的秒数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    def handle_signal(_signum, _frame) -> None:
        _stop.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    client = connect_redis()
    wait_for_kafka()
    producer = make_producer()
    found: queue.Queue = queue.Queue()
    ready = threading.Event()
    listener = threading.Thread(
        target=listen_replies,
        args=(args.conversation, found, ready),
        daemon=True,
    )
    listener.start()
    if not ready.wait(60):
        _stop.set()
        raise SystemExit(f"订阅 {TOPIC_REPLIES} 超时")

    try:
        if args.once is not None:
            send_and_wait(
                client,
                producer,
                args.conversation,
                require_content(args.once),
                found,
                args.timeout,
            )
        else:
            interactive(client, producer, args.conversation, found, args.timeout)
    except KeyboardInterrupt:
        print(flush=True)
    finally:
        _stop.set()
        listener.join(timeout=2)


if __name__ == "__main__":
    main()
