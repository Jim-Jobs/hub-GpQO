# 03 · Kafka 设计

> Kafka 在本系统中的定位：**异步任务管道 + 状态变更总线**。
> 问答链路**不走 Kafka**（交互式低延迟场景，消息队列语义是净负担）。

---

## 1. 为什么这样用

| 链路 | 是否走 Kafka | 理由 |
|---|---|---|
| 上传 → 解析 | ✅ | 典型慢任务（分钟级）：需要削峰、重试、可观测、可并行扩展 |
| 解析状态变更 → 前端 | ✅ | 天然的"事件总线"：一个状态变化要被多个订阅者（多个浏览器标签页）看到 |
| 用户提问 → 回答 | ❌ | 要求 1 秒内开始出字；Kafka 的消费组分配、offset、顺序保证在这里是负担而非收益 |

**一句话**：Kafka 用在**慢任务**上，不用在**聊天**上。

---

## 2. Topic 清单

| Topic | 分区 | 副本 | 生产者 | 消费者 | 语义 |
|---|---|---|---|---|---|
| `pdf.upload` | 2 | 1 | API | Worker | 至少一次 |
| `pdf.parse.status` | 1 | 1 | Worker / API | API 的 status-bus | 至少一次 |
| `pdf.upload.dlq` | 1 | 1 | Worker | 人工排查 | 至少一次 |

> 单机 KRaft 模式下副本因子固定为 1。将来扩到多机时改配置即可，代码无需变化。

**分区数为什么是 2？** 分区数 = 消费者并行度上限。当前设计是 1 个 Worker 消费（串行解析，避免 GPU/内存争抢），第 2 个分区是为了**未来并行**预留：把分区数改成 4、起 4 个 Worker，就能并行解析 4 份文档。key 用 `doc_id` 保证同一份文档的消息落同一分区（顺序性）。

---

## 3. 消息契约

### 3.1 `pdf.upload` —— 解析任务

```json
{
  "task_id": "t_9f3c1a7b2d40",
  "doc_id": "d_ab12cd34ef56",
  "filename": "2024年度财报.pdf",
  "content_hash": "9f2a...c41d",
  "file_path": "/data/uploads/d_ab12cd34ef56.pdf",
  "uploaded_at": "2026-11-11T09:30:00.123456+00:00",
  "attempt": 0
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 解析任务 ID，`t_` 前缀 + 12 位 hex |
| `doc_id` | string | 文档 ID，**也是 Kafka 的 message key** |
| `filename` | string | 原始文件名（展示用） |
| `content_hash` | string | PDF 的 SHA-256，用于去重与幂等校验 |
| `file_path` | string | 落盘后的绝对路径，Worker 据此读取 |
| `attempt` | int | 已重试次数，`>= worker_max_attempts` 时进 DLQ |

**序列化**：JSON（UTF-8，`ensure_ascii=false`，中文文件名不转义）。

### 3.2 `pdf.parse.status` —— 状态变更事件

```json
{
  "doc_id": "d_ab12cd34ef56",
  "status": "processing",
  "progress": 0.55,
  "stage": "caption",
  "error": null,
  "chunk_count": null,
  "image_count": null,
  "updated_at": "2026-11-11T09:34:20.881200+00:00"
}
```

**终态事件的补充字段**（`status=done` 时）：

```json
{
  "doc_id": "d_ab12cd34ef56",
  "status": "done",
  "progress": 1.0,
  "stage": "finished",
  "error": null,
  "chunk_count": 128,
  "image_count": 17,
  "updated_at": "2026-11-11T09:38:02.114000+00:00"
}
```

**失败事件**（`status=failed`）：

```json
{
  "doc_id": "d_ab12cd34ef56",
  "status": "failed",
  "progress": 0.25,
  "stage": "mineru_parse",
  "error": "MinerU 解析失败: 文件已加密 (task_id=t_9f3c...)",
  "updated_at": "2026-11-11T09:32:10.000000+00:00"
}
```

| 字段 | 说明 |
|---|---|
| `status` | `pending` / `processing` / `done` / `failed` |
| `progress` | 0.0 ~ 1.0，Worker 各阶段汇报 |
| `stage` | `queued` / `mineru_parse` / `chunking` / `caption` / `embedding` / `indexing` / `finished` |
| `error` | **用户可读**的错误原因（中文，含 MinerU 原始信息），前端直接展示 |
| `chunk_count` / `image_count` | 完成时统计 |

> **事件是"通知"不是"真相"**：真相永远在 ES 的 `documents.status`。事件可能重复、可能乱序（跨分区），但因为最终都写同一个 ES 字段，状态收敛。

### 3.3 `pdf.upload.dlq` —— 死信

消息体与 `pdf.upload` 相同，额外追加：

```json
{
  "task_id": "t_9f3c1a7b2d40",
  "doc_id": "d_ab12cd34ef56",
  "attempt": 3,
  "last_error": "MinerU 解析失败: 文件已加密",
  "failed_at": "2026-11-11T09:45:00+00:00"
}
```

---

## 4. 生产者配置

```python
KafkaProducer(
    bootstrap_servers=settings.kafka_bootstrap_servers,
    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
    key_serializer=lambda k: k.encode("utf-8") if k else None,
    acks="all",                 # 等所有 ISR 确认 → 配合幂等消费保证不丢
    retries=5,
    linger_ms=10,               # 少量攒批，降低网络往返
    max_request_size=10 * 1024 * 1024,
    request_timeout_ms=30000,
    api_version_auto_timeout_ms=10000,
)
```

| 参数 | 取值理由 |
|---|---|
| `acks="all"` | 单机副本因子为 1，`all` 与 `1` 等价，但配置迁移到多机时语义正确 |
| `key=doc_id` | 保证同一文档的消息进同一分区 → 顺序性 |
| `linger_ms=10` | 10ms 的攒批窗口，对上传这种低频操作延迟可忽略 |

---

## 5. 消费者配置与语义

```python
KafkaConsumer(
    "pdf.upload",
    bootstrap_servers=...,
    group_id="pdf-parser",
    enable_auto_commit=False,        # ← 关键：手动提交
    auto_offset_reset="earliest",   # 首次启动从头消费，不丢任务
    max_poll_records=1,             # 一次一条，配合手动提交
    value_deserializer=json.loads,
    key_deserializer=lambda b: b.decode(),
)
```

### 5.1 至少一次 + 幂等

```
┌─── Worker 主循环 ────────────────────────────────────────────┐
│                                                               │
│  1. poll() 取一条消息 msg                                      │
│  2. 解析失败？                                                  │
│       ├─ 是 → attempt+1，重发 pdf.upload；                     │
│       │      attempt >= 上限 → 发 pdf.upload.dlq + 状态 failed   │
│       │      → 提交 offset（不再重试这条）                      │
│       └─ 否 ↓                                                  │
│  3. 执行解析流水线：                                            │
│       MinerU → 切块 → caption → 向量化 → 写 ES                 │
│  4. 写 ES 成功？                                               │
│       ├─ 是 → 提交 offset ✅                                   │
│       └─ 否 → 不提交 offset（消息将被重放）→ 状态置 failed       │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

**为什么是"至少一次"而不是"至多一次"？** 至多一次会在 Worker 崩溃时**丢任务**（文档永远卡在 pending）。至少一次 + 幂等最稳妥。

**幂等如何保证？**

| 层级 | 幂等手段 |
|---|---|
| 消息重放 | 同一 `doc_id` 重复解析 → 重解析前先 `delete_document_content(doc_id)` 清掉旧块，再重新 bulk 写入 |
| 块 ID | `{doc_id}_c{seq:04d}`，`seq` 由内容顺序决定 → 同内容必得同 ID |
| 块写入 | ES `index` 操作（固定 `_id`）而非 `create` → 重复写覆盖而非报错 |
| 图片 ID | `{doc_id}_p{page}_i{idx}`，同上 |
| 状态写入 | 固定 `_id=doc_id`，后写覆盖先写 |

> **注意 chunk_id 不含内容 hash**：若含 hash，一旦调整切块参数就会生成全新 ID，旧块会残留。序号 + "重解析前先删"更简单可靠。

### 5.2 offset 提交时机

**先写 ES，成功后提交 offset。** 反过来（先提交后写）会在写 ES 前崩溃时丢数据。

代价：写 ES 成功但提交 offset 前崩溃 → 消息重放 → 重复解析（浪费一次 MinerU 调用），但数据正确。这是**正确性优先于效率**的取舍，对单用户小规模完全可接受。

---

## 6. 状态回传机制

```
Worker                              API
  │                                  │
  │ produce pdf.parse.status         │
  │─────────────────────────────────▶│ Kafka
  │                                  │
  │                    consume ◀────┤
  │                    status-bus     │
  │                       │          │
  │                       ├─▶ SSE 订阅者 A
  │                       ├─▶ SSE 订阅者 B
  │                       └─▶ SSE 订阅者 C
```

**status-bus 的实现要点**
- API 进程内启动一个后台协程消费 `pdf.parse.status`（独立 consumer group，如 `status-bus`）。
- 每个 SSE 连接注册一个 `asyncio.Queue`；bus 收到事件后**遍历所有订阅者**投递。
- 单个订阅者队列满时**丢弃该订阅者的这条事件**（慢客户端不能拖垮 bus）——反正下次 `GET /api/documents` 能拉到真相。
- 事件本身可能被 Kafka 重复投递 → bus 侧按 `(doc_id, updated_at)` 去重。

---

## 7. 运维

### 7.1 创建 topic（应用启动时也会自动创建）

```bash
docker exec -it mmrag-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create \
  --topic pdf.upload --partitions 2 --replication-factor 1

docker exec -it mmrag-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create \
  --topic pdf.parse.status --partitions 1 --replication-factor 1

docker exec -it mmrag-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create \
  --topic pdf.upload.dlq --partitions 1 --replication-factor 1
```

### 7.2 常用排查命令

```bash
# 查看消费组滞后
docker exec -it mmrag-kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --describe --group pdf-parser

# 观察消息流
docker exec -it mmrag-kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic pdf.parse.status --from-beginning --max-messages 20

# 查看死信
docker exec -it mmrag-kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic pdf.upload.dlq --from-beginning
```

### 7.3 关键指标

| 指标 | 含义 | 异常信号 |
|---|---|---|
| `consumer lag` on `pdf.upload` | 任务积压 | 持续 > 0 说明 Worker 卡住或解析太慢 |
| `pdf.upload.dlq` 消息数 | 重试耗尽的任务 | > 0 说明有文档持续失败 |
| `pdf.parse.status` 速率 | 状态事件频率 | 骤降说明 Worker 挂了 |

---

## 8. 容量估算

单用户、< 50 份 PDF：

| 项 | 估算 |
|---|---|
| `pdf.upload` 消息总量 | < 50 条（+ 重试） |
| `pdf.parse.status` 消息总量 | < 50 × 6 ≈ 300 条 |
| 单条消息大小 | < 1 KB |
| Kafka 磁盘占用 | < 1 MB（远小于日志保留所需的 1GB 默认配额） |
| 保留策略 | `retention.ms=604800000`（7 天）足够；`pdf.upload` 可设 `delete` 而非 `compact` |

**结论**：Kafka 在本项目中的"成本"主要是**运维复杂度**而非资源成本。因此部署要尽量简化：单节点 KRaft、无认证（仅监听 127.0.0.1）、无监控套件。
