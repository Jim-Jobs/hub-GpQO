# S4 真实验收记录

- 日期：2026-11-10
- 范围：Kafka 异步解析、MinerU 适配、真实向量入库、状态四态、重复消息幂等
- 结论：**S4 通过**（Milvus 以 Lite server 替代；Docker Milvus standalone 作为后续部署项）

## 1. 环境

| 组件 | 版本 / 方式 |
|---|---|
| Python | 3.12.13（conda `py312`） |
| Kafka | `apache/kafka:3.9.0`，Docker，`localhost:9092` |
| MinerU | 4.1.7，`mineru server` + `parse/read`，`tier=flash` |
| 文本向量 | `BAAI/bge-small-zh-v1.5`，512 维 |
| 图像/文本向量 | `OFA-Sys/chinese-clip-vit-base-patch16`，512 维 |
| 向量库 | Milvus Lite `3.2.2`，`milvus-lite server`，gRPC 19535 |
| 客户端 | `pymilvus 3.0.1` |

## 2. 三份完整 PDF 与四态

样本：

1. `77e4...pdf`：26 页，62 chunks
2. `9f06...pdf`：17 页，39 chunks
3. `full3.pdf`：26+17 页合并，43 页，101 chunks

轮询记录（约 200ms 间隔）：

```text
file 1: PENDING -> PARSING -> DONE（26 页 / 62 chunks）
file 2: PENDING -> PARSING -> DONE（17 页 / 39 chunks）
file 3: PENDING -> PARSING -> DONE（43 页 / 101 chunks）
```

向量总量：**202**。

证据：

- `data/s4-accept-20261110-154913/`
- `data/s4-upload-result.json`

## 3. 真实 Kafka 重复消息回放

对已 `DONE` 的 file 1 重新投递同一条 task payload（同 key、同 topic）：

```text
vectors_before = 202
vectors_after  = 202
status         = DONE
chunk_count    = 62 -> 62
worker log     = file_id=1 result=SKIP_DONE
```

证据：

- `data/s4-replay-result.json`
- `data/s4-accept-20261110-154913/logs/worker.log`

## 4. FAILED 态与重解析

独立失败路径已验证：

- Worker 使用不存在的 MinerU 后端 → `FAILED`，`error_code=MINERU_NOT_INSTALLED`；
- DLQ 写入 `worker_dlq.jsonl`；
- 切换 mock Worker 后重新解析 → `PENDING → DONE`。

证据：`data/flow-failure-20261110-141638/`。

## 5. 决策：接受 Milvus Lite server 替代

Docker Milvus 2.4 的 MinIO 官方镜像/二进制已下架，且 etcd/Milvus 镜像在当前 registry 被拒绝，无法完成 Docker standalone 验收。经确认，S4 接受：

```text
Milvus Lite server（milvus-lite 3.2.2，gRPC）作为本次验收的向量库实现
```

约束与已知问题：

- 单个 `.db` 文件只能被一个进程持有；API + Worker 必须用 `milvus-lite server`，不能各自打开同一个文件；
- `pymilvus 3.0.1` 会打印 `AllocTimestamp Method not implemented` 的 gRPC 噪音，但集合创建、写入、检索均成功；
- Lite 不是生产集群方案；Docker Milvus 2.4 / Zilliz Cloud 仍是后续部署目标。

## 6. 复现要点

```powershell
# Milvus Lite server
milvus-lite server --data-dir data\milvus-lite-s4 --host 127.0.0.1 --port 19535
$env:MILVUS_URI="http://127.0.0.1:19535"

# Kafka
docker compose -f deploy/compose.yaml up -d kafka

# MinerU
mineru server start

# API / Worker
uvicorn app.main:app --port 8000
python -m app.worker
```

测试：`python -m pytest -q` → **82 passed**。
