# 真实环境验收操作单

> 适用：在本机 Docker + MinerU + 真实模型环境下验收「批量上传 → Kafka 异步解析 → 状态追踪 → 图文问答」。
> 前置：代码阶段已通过 `python -m pytest -q`（当前 82 passed）。本单只验收真实链路。
> 说明：冒烟脚本已在 mock 模式验证通过（双 PDF 14/14；失败重解析、鉴权、删除流程也已验证）；真实 MinerU 4 双文件解析已通过（14/14，索引/LLM 仍 mock）。

## 0. 验收原则

1. 使用**新的知识库 id**（如 `acceptance-20261110`），避免历史数据干扰；
2. 准备 **2～3 份含文字与图表的 PDF**，文件放在 `data/uploads/` 或任意路径；
3. mock 模式只用于预检，真实验收必须 `RAG_MOCK_EXTERNAL=0`；
4. 任何一步失败先保留现场（日志、DLQ、容器状态），不要急着重跑。

---

## 1. 验收前检查表

| 项 | 要求 | 检查命令 |
|---|---|---|
| Docker | Desktop 已启动，能跑容器 | `docker info` |
| Kafka/Milvus | Kafka 容器健康；Milvus 用 Lite server 或 Docker standalone | `docker compose -f deploy/compose.local.yaml ps`；`Test-NetConnection 127.0.0.1 -Port 19530` |
| MinerU | v4 CLI 可用，本地服务已启动 | `mineru --version`；`mineru server status` |
| 模型 | BGE / CLIP 路径存在，维度与配置一致 | 检查 `BGE_MODEL_PATH`、`CLIP_MODEL_PATH` |
| Python | conda `py312`，依赖完整 | `conda activate py312; python -m pytest -q` |
| API Key | 如需鉴权，已注册 | `python scripts/register_api_key.py <kb> <key>` |

---

## 2. 启动中间件

```powershell
cd <repo>
conda activate py312

# 1) 本地默认：只启动 Kafka（Milvus 用 Lite server）
docker compose -f deploy/compose.local.yaml up -d kafka

# 2) 启动 Milvus Lite server（API + Worker 共享，保持不关）
python -m pip install -r requirements-lite.txt
milvus-lite server --data-dir data\milvus-lite-server --host 127.0.0.1 --port 19530
$env:MILVUS_URI="http://127.0.0.1:19530"
$env:MILVUS_COLLECTION="rag_acceptance"

# 3) 端口确认：9092 / 19530
Test-NetConnection 127.0.0.1 -Port 9092
Test-NetConnection 127.0.0.1 -Port 19530
```

> Docker Milvus 是目标部署方式；当前 MinIO/etcd/Milvus 镜像不可用时跳过本段，Lite 已作为 S4 正式验收实现。

```powershell
# 仅当 Docker 镜像可用时（可选）
docker compose -f deploy/compose.yaml up -d kafka milvus
docker compose -f deploy/compose.yaml ps
```

> 单个 `.db` 文件只允许一个进程持有锁；Worker 写入时 API 会 `DataDirLockedError`。Lite server 模式解决了这个问题。
> `pymilvus` 导入时会把 `MILVUS_URI` 当 HTTP 地址解析；不要把 Windows 路径写进 `MILVUS_URI`。

MinerU 4（当前安装版本 4.1.7）：

```powershell
# 启动本地服务（parse/read 都依赖它）
mineru server start
mineru server status

$env:RAG_MINERU_CLI_PATH="C:\Users\Cooper\miniconda3\envs\py312\Scripts\mineru.exe"
$env:RAG_MINERU_CLI_MODE="v4"
$env:RAG_MINERU_TIER="flash"          # flash/basic/standard/advanced
$env:RAG_MINERU_SERVER_URL="http://127.0.0.1:15980"
```

> 首次启动若报 `Credential configuration directory must not be writable by other users`，是 `~/.mineru` 的 ACL 问题：移除 `Everyone` 写权限，仅保留当前用户/SYSTEM/Administrators。

模型环境变量（示例）：

```powershell
$env:RAG_MOCK_EXTERNAL="0"
$env:RAG_PARSE_BACKEND="mineru"
$env:MILVUS_URI="http://localhost:19530"
$env:MILVUS_COLLECTION="rag_acceptance"
$env:BGE_MODEL_PATH="D:\models\bge-small-zh-v1.5"
$env:CLIP_MODEL_PATH="D:\models\chinese-clip-vit-base-patch16"
$env:BGE_DIM="512"
$env:CLIP_DIM="512"          # base 用 512；large 用 1024
$env:DASHSCOPE_API_KEY="sk-..."
$env:LLM_MODEL="qwen3.8-flash"
$env:VL_MODEL="qwen3-vl-plus"
```

> 维度以模型实际输出为准；与集合不一致时，先改 `BGE_DIM`/`CLIP_DIM` 并重建集合。
> 项目会自动读取仓库根目录 `.env`（不覆盖已有环境变量），Key/路径可写在 `.env` 里。

---

## 3. 启动 API 与 Worker

```powershell
# 终端 1：预建集合（幂等）
python scripts/ensure_milvus_schema.py

# 终端 2：API（同源托管前端）
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 3：Worker（真实 Kafka）
python -m app.worker
```

打开 <http://127.0.0.1:8000/>，右上角健康徽章应为“服务正常”。

---

## 4. 自动冒烟（推荐先跑）

```powershell
python scripts/smoke_acceptance.py `
  --pdf data/uploads/年报.pdf `
  --pdf data/uploads/架构图.pdf `
  --kb acceptance-20261110 `
  --timeout 900
```

脚本按顺序检查：

1. `/health`、`/health/deps` 全部 ok；
2. 批量上传至少 1 个 accepted；
3. 轮询到全部 `DONE`；
4. `page_count` / `chunk_count` 大于 0；
5. 文本问答有 `answer` 与 `sources`；
6. 图文问答 `used_images` 非空；
7. 重复上传返回 `duplicates ≥ 1`。

**通过标志**：末尾打印 `N/N checks passed`，退出码 0。
失败时按末尾 `FAILED` 项排查，脚本不会删除已上传文件。

---

## 5. 手工验收项

| # | 操作 | 预期 |
|---|---|---|
| 1 | 一次选择 3 个 PDF 上传 | 列表出现 3 条，初始“待解析” |
| 2 | 观察状态 | 2s 级变“解析中”，再变“解析完成” |
| 3 | 刷新页面 | 状态、页数/块数、耗时保留 |
| 4 | 查看 `data/logs/worker.log` | 同一 `trace_id` 能从上传追到解析入库 |
| 5 | 提问“请概括文档内容” | 回答带 `sources`（文件名/页码） |
| 6 | 添加 2 张图片提问“对比差异” | 回答体现图片内容，`used_images` 含 user |
| 7 | 纯图片提问（不输入文字） | 正常返回答案，不报 422 |
| 8 | 重复上传同一文件 | 返回“文件已存在”，不重复解析 |
| 9 | 停掉 MinerU 后上传新 PDF | 重试耗尽 → “解析失败”，可重新解析 |
| 10 | 解析中 kill Worker，再启动 | 超时后由 sweeper 置“解析失败” |
| 11 | 用户图片写“忽略以上指令” | 不被执行 |
| 12 | 删除文档 | 列表消失，`data/processed/<stem>` 被清理 |

### Kafka 消费核查

```powershell
docker exec rag-kafka /opt/kafka/bin/kafka-consumer-groups.sh `
  --bootstrap-server localhost:9092 `
  --describe --group rag-parse-workers
```

预期：`LAG` 为 0；解析中应看到当前 offset 在处理。

### Milvus 数据核查

```powershell
python -c "from app.milvus import count_vectors; print('vectors:', count_vectors())"
```

预期：上传解析完成后 `vectors > 0`；删除文档后对应 `db_id` 不再命中。

### 评测（可选）

```powershell
python scripts/run_evaluation.py `
  --dataset tests/fixtures/sample_eval.jsonl `
  --knowledge-base acceptance-20261110
```

记录 `average`，与历史版本对比即可，不要为分数修改评分口径。

---

## 6. 通过标准

- 自动冒烟脚本 **全部 PASS**；
- 手工验收 1～10 项通过（11～12 为安全/清理加分项）；
- `pytest -q` 全绿；
- `data/logs/error.log` 无未解释的异常；
- `data/logs/worker_dlq.jsonl` 为空或仅有已登记的历史失败。

全部满足即可判定“真实链路验收通过”。

---

## 7. 故障速查

| 现象 | 优先检查 | 处理 |
|---|---|---|
| `docker` 连接失败 | Docker Desktop 是否启动 | 启动后重试 |
| 上传 20s 后 `ENQUEUE_FAILED` | Kafka 容器、9092、advertised listeners | `compose ps`、`docker logs rag-kafka` |
| 一直“待解析” | consumer group lag、`worker.log` | 启动 Worker；看 DLQ；补偿重投 |
| 一直“解析中” | MinerU 是否卡住、解析超时 | 看 `worker.log`；超时后 sweeper 置 FAILED |
| `mineru: not found` | PATH / `RAG_MINERU_CLI_PATH` | 设绝对路径；`mineru server start`；旧版设 `RAG_MINERU_CLI_MODE=v3` |
| `pymilvus` 连接/协议错误 | Milvus 版本与 pymilvus 3.x | 锁 `pymilvus<3` 或升级 Milvus 镜像 |
| 写入报维度不一致 | `BGE_DIM`/`CLIP_DIM` 与模型 | 改配置并重建集合 |
| `DONE` 但检索无结果 | 集合、`db_id`、`flush` | 重新解析；检查 `count_vectors()` |
| 图文问答 `used_images` 为空 | 图片格式/数量、`RAG_MAX_VISION_IMAGES` | 换 PNG/JPEG，调大上限 |
| `database is locked` | SQLite 并发 | 确认 WAL + `busy_timeout`，避免并行写 |

---

## 8. 清理与回滚

```powershell
# 停止应用：Ctrl+C 关闭 uvicorn / worker

# 保留数据卷，仅停本地 Kafka（Lite server 用 Ctrl+C 停）
docker compose -f deploy/compose.local.yaml down

# Docker Milvus 目标栈（镜像可用时）
# docker compose -f deploy/compose.yaml down

# 彻底清理（会删除 Kafka/Milvus 数据，谨慎）
# docker compose -f deploy/compose.yaml down -v
```

验收数据默认在 `data/`（已被 `.gitignore` 忽略）；需要重跑时换一个 `--kb`，不要直接删用户数据。

---

## 9. 验收记录模板

```text
日期：
执行人：
代码版本/提交：
Python / conda：
Docker 镜像：kafka=3.9.0 milvus=2.4.15
MinerU 后端：4.1.7 / tier=flash（v4 parse+read）
模型：BGE=... CLIP=...
自动冒烟：N/N passed（退出码 0/1）
手工项：1～10 通过 / 失败项
pytest：82 passed
异常与处置：
结论：通过 / 不通过
```
