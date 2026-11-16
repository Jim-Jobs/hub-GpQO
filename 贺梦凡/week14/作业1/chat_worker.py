"""消费 chat.events，把对话写入 Redis，并把回复发到 chat.replies。

这是历史的唯一写入方。偏移量在 Redis 写成功并且回复已发出之后才提交。
"""

import json
import signal
import time

import redis
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from chat_common import (
    KAFKA_BOOTSTRAP,
    TOPIC_EVENTS,
    TOPIC_REPLIES,
    connect_redis,
    dumps,
    ensure_stored,
    is_published,
    loads,
    mark_published,
    new_message,
)

REQUIRED_FIELDS = ("msg_id", "conversation_id", "role", "content", "ts")
_running = True


def _stop(signum, _frame) -> None:
    global _running
    _running = False
    print(f"收到信号 {signum}，准备退出", flush=True)


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


def make_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": "chat-worker",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": 600000,
        }
    )


def make_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
            "enable.idempotence": True,
            "max.in.flight.requests.per.connection": 5,
        }
    )


def publish(producer: Producer, message: dict) -> None:
    delivered: dict = {}

    def on_delivery(err, _msg) -> None:
        if err is not None:
            delivered["err"] = err
        else:
            delivered["ok"] = True

    producer.produce(
        TOPIC_REPLIES,
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
        raise RuntimeError("回复已提交，但没有收到 Kafka 回执")


def reply_for(user_message: dict) -> dict:
    # 回复 id 由用户消息 id 推出，worker 重放同一条事件时不会生成第二条回复。
    return new_message(
        conversation_id=user_message["conversation_id"],
        role="assistant",
        content=f"已收到：{user_message['content']}",
        msg_id=f"reply-{user_message['msg_id']}",
        reply_to=user_message["msg_id"],
    )


def handle(client, producer: Producer, message: dict) -> None:
    if ensure_stored(client, message):
        print(
            f"写入历史 {message['conversation_id']} {message['role']}: {message['content']}",
            flush=True,
        )
    else:
        print(f"重复消息已忽略 {message['msg_id']}", flush=True)

    if message["role"] != "user":
        return

    reply = reply_for(message)
    if ensure_stored(client, reply):
        print(
            f"写入历史 {reply['conversation_id']} {reply['role']}: {reply['content']}",
            flush=True,
        )
    if not is_published(client, reply["msg_id"]):
        publish(producer, reply)
        mark_published(client, reply["msg_id"])
        print(f"已发送回复 {reply['msg_id']}", flush=True)


def process_record(client, producer: Producer, record) -> None:
    raw = record.value()
    if raw is None:
        print("跳过空消息", flush=True)
        return
    try:
        message = loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"跳过无法解析的消息: {exc}", flush=True)
        return
    if not isinstance(message, dict) or any(field not in message for field in REQUIRED_FIELDS):
        print(f"跳过字段不完整的消息: {message}", flush=True)
        return

    while _running:
        try:
            handle(client, producer, message)
            return
        except (redis.exceptions.RedisError, KafkaException, RuntimeError) as exc:
            print(f"处理失败，2 秒后重试同一条消息: {exc}", flush=True)
            time.sleep(2)
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    client = connect_redis()
    wait_for_kafka()
    consumer = make_consumer()
    producer = make_producer()

    def on_assign(_consumer, partitions) -> None:
        print(f"worker 分配到 {len(partitions)} 个分区", flush=True)

    consumer.subscribe([TOPIC_EVENTS], on_assign=on_assign)
    print(f"worker 已启动，消费 {TOPIC_EVENTS}", flush=True)

    try:
        while _running:
            record = consumer.poll(1.0)
            if record is None:
                continue
            if record.error():
                if record.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"Kafka 错误: {record.error()}", flush=True)
                continue
            process_record(client, producer, record)
            consumer.commit(message=record, asynchronous=False)
    finally:
        consumer.close()
        print("worker 已退出", flush=True)


if __name__ == "__main__":
    main()
