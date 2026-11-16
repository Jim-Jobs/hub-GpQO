"""在本机演示 Kafka 广播、消费组并行处理及 Redis 历史记录。"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import redis
from dotenv import load_dotenv
from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError, TopicAlreadyExistsError


TOPICS = ("demo.topic.1", "demo.topic.2", "demo.topic.3")
TOPIC_NAMES = ("主题一", "主题二", "主题三")
CHINESE_NUMBERS = "一二三四五六"
MESSAGES_PER_PRODUCER = 6
MESSAGE_INTERVAL = 0.2
PROCESS_SECONDS = 1
TOTAL_MESSAGES = MESSAGES_PER_PRODUCER * 2
LOG_MARKS = {
    "准备": "[-]",
    "发送": "[+]",
    "处理": "[>]",
    "完成": "[v]",
    "进度": "[#]",
    "Redis": "[@]",
    "汇总": "[=]",
    "错误": "[!]",
}


class DemoState:
    """集中管理跨线程的计数与终端输出，避免日志交错。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.completed = {name: 0 for name in ("G1", "G2", "G3", "G4", "P1", "P2")}
        self.published = 0
        self.errors: list[str] = []

    def log(self, kind: str, message: str) -> None:
        """每行固定带有类型标记，便于观察并发输出。"""
        with self.lock:
            print(f"[{time.strftime('%H:%M:%S')}] {LOG_MARKS[kind]} [{kind}] {message}", flush=True)

    def record_publish(self) -> None:
        with self.lock:
            self.published += 1
            published = self.published
            processed = {group: self.completed[group] for group in ("G1", "G2", "G3", "G4")}
        if published % 3:
            return
        self.log(
            "进度",
            f"已发布 {published}/{TOTAL_MESSAGES}；"
            + "，".join(f"{group} 待处理约 {published - count}" for group, count in processed.items())
        )

    def record_completion(self, group: str, worker: str, message: dict, expected: int) -> None:
        with self.lock:
            self.completed[group] += 1
            count = self.completed[group]
        self.log("完成", f"{worker} 处理完成 {message['sender']} 的第{CHINESE_NUMBERS[message['sequence'] - 1]}条消息；{group} {count}/{expected}")

    def fail(self, worker: str, error: Exception) -> None:
        with self.lock:
            self.errors.append(f"{worker}: {error}")
        self.stop.set()
        self.log("错误", f"{worker} 出错：{error}")


def start_local_services() -> None:
    """一条 Python 命令启动本地服务；Docker Desktop 需已运行。"""
    compose = Path(__file__).with_name("docker-compose.yml")
    try:
        subprocess.run(["docker", "compose", "-f", str(compose), "up", "-d"], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("无法启动本机服务：请确认 Docker Desktop 已启动且 Linux 引擎可用") from error


def wait_for_services(servers: str, client: redis.Redis) -> None:
    """首次拉起 Kafka 可能较慢，在两项服务均可用后再创建主题。"""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            client.ping()
            admin = KafkaAdminClient(bootstrap_servers=servers, request_timeout_ms=3000)
            admin.close()
            return
        except (redis.RedisError, KafkaError, OSError):
            time.sleep(2)
    raise TimeoutError("Redis 或 Kafka 在 90 秒内没有就绪，请检查 docker compose ps/logs")


def create_topics(servers: str) -> None:
    """三个主题各有三个分区，单节点示例的副本数只能为一。"""
    admin = KafkaAdminClient(bootstrap_servers=servers)
    try:
        for topic in TOPICS:
            try:
                admin.create_topics(
                    {topic: {"num_partitions": 3, "replication_factor": 1}},
                    wait_for_metadata=True,
                )
                print(f"[-] [准备] 已创建 {topic}（3 个分区）")
            except TopicAlreadyExistsError:
                print(f"[-] [准备] 复用已有主题 {topic}")
            admin.wait_for_topics([topic])
            partitions = admin.describe_topics([topic])[0]["partitions"]
            if len(partitions) != 3:
                raise RuntimeError(f"{topic} 已有 {len(partitions)} 个分区，需要 3 个分区")
    finally:
        admin.close()


def consume(servers: str, client: redis.Redis, state: DemoState, group: str, worker: str, ready: threading.Event) -> None:
    """同组实例分摊分区；不同组各自读取全部消息。"""
    expected = MESSAGES_PER_PRODUCER if group in ("P1", "P2") else TOTAL_MESSAGES
    consumer = None
    try:
        consumer = KafkaConsumer(
            *TOPICS,
            bootstrap_servers=servers,
            group_id=f"{state.run_id}-{group}",
            client_id=f"{state.run_id}-{threading.current_thread().name}",
            # 每次运行使用新组 ID；从头读并按 run_id 过滤，避免订阅重平衡期间漏掉本次消息。
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            max_poll_records=1,
            max_poll_interval_ms=60000,
        )
        while not state.stop.is_set() and not consumer.assignment():
            consumer.poll(timeout_ms=200)
        ready.set()
        while not state.stop.is_set():
            with state.lock:
                if state.completed[group] >= expected:
                    break
            for records in consumer.poll(timeout_ms=200).values():
                for record in records:
                    message = json.loads(record.value.decode("utf-8"))
                    if message.get("run_id") != state.run_id or message.get("sender") == group:
                        consumer.commit()
                        continue
                    state.log(
                        "处理",
                        f"{worker} 正在处理 {message['sender']} 的第{CHINESE_NUMBERS[message['sequence'] - 1]}条消息"
                        f"（{TOPIC_NAMES[TOPICS.index(record.topic)]}，分区 {record.partition}）"
                    )
                    time.sleep(PROCESS_SECONDS)
                    if group == "G1":
                        # 只有共享组落库，避免独立组对同一消息重复写历史。
                        client.rpush(f"demo:history:{state.run_id}", json.dumps(message, ensure_ascii=False))
                    consumer.commit()
                    state.record_completion(group, worker, message, expected)
    except Exception as error:
        state.fail(worker, error)
    finally:
        ready.set()
        if consumer is not None:
            consumer.close()


def publish(servers: str, state: DemoState, sender: str, start_topic: int) -> None:
    """P1/P2 独立定时发送；订阅由另一个线程同时执行。"""
    producer = None
    try:
        producer = KafkaProducer(bootstrap_servers=servers)
        for sequence in range(1, MESSAGES_PER_PRODUCER + 1):
            if state.stop.is_set():
                break
            topic_index = (start_topic + sequence - 1) % len(TOPICS)
            message = {"run_id": state.run_id, "sender": sender, "sequence": sequence, "topic": TOPICS[topic_index]}
            # 固定轮流使用三个分区，确保 G1 的三个实例都有消息可处理。
            payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
            metadata = producer.send(TOPICS[topic_index], payload, partition=(sequence - 1) % 3).get(timeout=10)
            state.log(
                "发送",
                f"{sender} 第{CHINESE_NUMBERS[sequence - 1]}次发送消息至{TOPIC_NAMES[topic_index]}"
                f"（分区 {metadata.partition}，偏移量 {metadata.offset}）"
            )
            state.record_publish()
            time.sleep(MESSAGE_INTERVAL)
    except Exception as error:
        state.fail(sender, error)
    finally:
        if producer is not None:
            producer.close()


def run_workers(servers: str, client: redis.Redis, state: DemoState) -> None:
    """先等全部订阅者分配分区，再开始定时发送和追赶。"""
    workers = [("G1", f"G1-实例{n}") for n in range(1, 4)]
    workers += [(group, f"{group}-实例1") for group in ("G2", "G3", "G4")]
    workers += [(group, f"{group}-订阅者") for group in ("P1", "P2")]
    ready_events = []
    threads = []
    for group, worker in workers:
        ready = threading.Event()
        thread = threading.Thread(
            target=consume,
            args=(servers, client, state, group, worker, ready),
            name=f"{group}-{len(threads) + 1}",
            daemon=True,
        )
        thread.start()
        ready_events.append(ready)
        threads.append(thread)
    for ready in ready_events:
        if not ready.wait(timeout=30) or state.errors:
            state.stop.set()
            raise RuntimeError(f"订阅者启动失败：{state.errors or '分区分配超时'}")
    state.log("准备", "八个订阅实例均已分配分区；P1/P2 开始同时发送和接收")
    for sender, first_topic in (("P1", 0), ("P2", 1)):
        thread = threading.Thread(target=publish, args=(servers, state, sender, first_topic), daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + 45
    for thread in threads:
        while thread.is_alive() and time.monotonic() < deadline and not state.errors:
            thread.join(timeout=0.2)
    state.stop.set()
    if state.errors:
        raise RuntimeError("；".join(state.errors))
    if any(thread.is_alive() for thread in threads):
        raise TimeoutError("等待消费者追赶超时，请查看上面的处理进度")


def show_summary(client: redis.Redis, state: DemoState) -> None:
    """从 Redis 重新读取记录，以实际落库条数验证 G1 的处理结果。"""
    key = f"demo:history:{state.run_id}"
    history = [json.loads(raw) for raw in client.lrange(key, 0, -1)]
    state.log("Redis", f"历史键：{key}，读取到 {len(history)} 条记录")
    for group, count in state.completed.items():
        state.log("汇总", f"{group} 已处理 {count} 条")
    expected_messages = {(sender, sequence) for sender in ("P1", "P2") for sequence in range(1, MESSAGES_PER_PRODUCER + 1)}
    actual_messages = {(message["sender"], message["sequence"]) for message in history}
    if len(history) != TOTAL_MESSAGES or actual_messages != expected_messages or any(
        count != (MESSAGES_PER_PRODUCER if group in ("P1", "P2") else TOTAL_MESSAGES)
        for group, count in state.completed.items()
    ):
        raise RuntimeError("演示结束但处理数量不一致")
    state.log("汇总", "所有消费组已追赶完成；Redis 保存了全部消息")


def main() -> None:
    """读取本地配置并编排一次完整演示。"""
    load_dotenv(Path(__file__).with_name(".env"))
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    start_local_services()
    wait_for_services(servers, client)
    create_topics(servers)
    state = DemoState(uuid.uuid4().hex[:8])
    run_workers(servers, client, state)
    show_summary(client, state)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TimeoutError) as error:
        print(f"演示未完成：{error}", file=sys.stderr)
        raise SystemExit(1) from error
