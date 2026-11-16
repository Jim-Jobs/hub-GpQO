# 技术说明

## 服务与配置

`offline/docker-images.tar` 保存 Redis 与 Kafka 镜像，`offline/wheels/` 保存当前 Python 依赖。接收方先在本地执行 `docker load` 和离线 `pip install`；Docker Desktop 与 Python 3.12 本身需要预装。镜像归档来自本机 Docker Linux/amd64 环境，适合相同架构的电脑。

`demo.py` 执行 `docker compose up -d`，然后等待 Redis 与 Kafka 就绪。Docker Desktop 必须已经运行。Compose 将 Redis 的 6379 端口和 Kafka 的 9092 端口只绑定到本机 `127.0.0.1`。Python 程序通过项目 `.env` 中的 `REDIS_URL` 和 `KAFKA_BOOTSTRAP_SERVERS` 连接；默认值分别为 `redis://localhost:6379/0`、`127.0.0.1:9092`。Kafka 对外也通告 `127.0.0.1:9092`，避免 Windows 将 `localhost` 优先解析为未监听的 IPv6 `::1`。

Kafka 为单节点 KRaft 演示环境。三个固定主题 `demo.topic.1`、`demo.topic.2`、`demo.topic.3` 各有三个分区，副本数为一。这能演示分区和消费组，但不提供多节点容错。程序为每次运行生成独立的 `run_id` 和消费组 ID；旧消息按 `run_id` 过滤。

## 消息路线

P1 从主题一开始，P2 从主题二开始；双方依次轮换三个主题，各发送六条消息。每个生产者有独立发送线程和订阅线程。订阅线程只打印并处理对方的消息。每条消息包含本次运行 ID、发送者、发送序号、主题。发送者显式选择分区，让 G1 的三个实例都有消息可处理。

G1 有三个实例，使用同一个 `group_id`，每个分区同一时刻只分给组内一个实例。G2、G3、G4 各有一个实例和各自的 `group_id`，它们各自处理全部消息。P1 和 P2 的订阅者另有各自的 `group_id`。所有订阅者都订阅三个主题；每条消息模拟处理一秒，处理后手动提交偏移量。终端的“待处理约”由“已发布数量 − 当前组已完成数量”计算，是演示用的近似积压量，不是 Kafka Broker 查询到的真实 lag。

只有 G1 在处理后把 JSON 写入 Redis List `demo:history:<run_id>`。结束时重新读取该键并核对 12 条记录。Redis 启用了 AOF 与 Compose 命名数据卷，因此 `docker compose down` 后记录仍可保留；`docker compose down -v` 会删除数据卷。正常演示应恰好写入 12 条；若在 Redis 写入后、Kafka 提交偏移量前发生故障，重新消费可能造成重复写入。这是“至少一次”处理的常见边界，本示例未做去重。

若本机已经存在同名主题，程序会复用它们。它们必须仍有至少三个分区；若不是，请先处理旧主题或修改本地环境。演示结束后容器保持运行，便于检查 Redis 数据。可通过 `docker compose ps` 和 `docker compose logs` 查看服务状态。

