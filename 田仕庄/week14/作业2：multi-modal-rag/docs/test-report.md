# 多模态 RAG 系统测试报告

| 项目 | 内容 |
|---|---|
| 报告编号 | TR-20261110-01 |
| 测试日期 | 2026-11-10 |
| 被测版本 | `v0.1.0` 工作区快照（未初始化 Git） |
| 测试对象 | Kafka 异步解析 + Milvus 检索 + Qwen 图文问答 |
| 测试结论 | **通过（有条件）** |

## 1. 摘要

本次测试覆盖单元测试、接口集成测试、真实 Kafka/MinerU/向量模型/LLM 链路、本地一键启动与停止。

核心结论：

- 自动化测试 **82 passed**（49 单元 + 33 集成），耗时 2.42s；
- 真实 S4 链路通过：3 份完整 PDF 走完 `PENDING → PARSING → DONE`，共 86 页、202 个向量；
- 真实 Kafka 重复消息回放返回 `SKIP_DONE`，向量数 202→202、chunk 数不变；
- 真实文本/图文 LLM 冒烟 **14/14**，文本 sources=5，图文 used_images=4；
- 一键启动脚本冷启动 7/7 步通过，`/health/deps` 全部 ok；
- 已正式接受 **Milvus Lite server** 作为 S4 向量库实现，Docker Milvus standalone 列为后续部署项。

## 2. 测试环境

| 组件 | 版本 / 配置 |
|---|---|
| 操作系统 | Windows 10/11 + Docker Desktop 29.9.2 |
| Python | 3.12.13（conda `py312`） |
| Kafka | `apache/kafka:3.9.0`，KRaft，`localhost:9092` |
| MinerU | 4.1.7，`mineru server` + `parse/read`，`tier=flash` |
| 文本向量 | `BAAI/bge-small-zh-v1.5`，512 维 |
| 图文向量 | `OFA-Sys/chinese-clip-vit-base-patch16`，512 维 |
| 向量库 | Milvus Lite 3.2.2，`milvus-lite server`，gRPC 19530 |
| 向量客户端 | `pymilvus 3.0.1` |
| 文本 LLM | `qwen3.8-flash` |
| 图文 LLM | `qwen3-vl-plus` |
| 前端 | 原生 HTML/JS/CSS，FastAPI 同源托管 |

## 3. 测试范围

| 层次 | 内容 |
|---|---|
| 单元测试 | 状态机、上传去重、解析器、分块、向量封装、RRF、图文 parts、图片归一化、评测、日志、DB、清理 |
| 接口集成 | 鉴权、文档列表/状态/重解析/删除、文本问答、图文问答、Key 管理、Worker、Sweeper、前端静态资源 |
| 真实链路 | Kafka 异步入队 → MinerU 4 解析 → BGE/CLIP 编码 → Milvus 写入/flush → 检索 → Qwen |
| 运维 | 一键启动/停止、健康检查、日志、DLQ、重复消息幂等 |

不在本次范围：压力/并发极限、长时间稳定性、安全渗透、Kubernetes/多副本、Docker Milvus standalone、MinerU 本地 `pipeline` 模型。

## 4. 自动化测试结果

**执行命令**：`python -m pytest -q`

**结果**：`82 passed in 2.42s`

### 4.1 单元测试（49）

| 测试文件 | 用例数 |
|---|---:|
| `test_state_machine.py` | 6 |
| `test_upload.py` | 6 |
| `test_parser.py` | 3 |
| `test_parser_content_list.py` | 2 |
| `test_parser_v4.py` | 2 |
| `test_chunker.py` | 2 |
| `test_embeddings.py` | 4 |
| `test_milvus.py` | 1 |
| `test_retrieval.py` | 2 |
| `test_parts.py` | 4 |
| `test_normalize.py` | 4 |
| `test_evaluation.py` | 4 |
| `test_cleanup.py` | 2 |
| `test_health.py` | 3 |
| `test_logging.py` | 2 |
| `test_db.py` | 2 |

### 4.2 集成测试（33）

| 测试文件 | 用例数 |
|---|---:|
| `test_auth.py` | 4 |
| `test_documents_api.py` | 4 |
| `test_chat.py` | 5 |
| `test_chat_images.py` | 5 |
| `test_kb.py` | 3 |
| `test_worker.py` | 5 |
| `test_sweeper.py` | 4 |
| `test_frontend.py` | 3 |

### 4.3 关键覆盖点

- 状态迁移矩阵、乐观锁竞争、非法迁移拒绝；
- 批量上传部分失败、重复上传不重复入队；
- MinerU 4 `page N of M` 页码与 `doc:` 图片提取；
- 图文 parts 顺序、占位符、图片预算；
- 鉴权 401/403、Key 注册/重复/长度校验；
- 删除、重解析、sweeper 补偿、Worker `SKIP_DONE`；
- 前端 `/`、CSS/JS 静态资源可访问。

## 5. 真实链路测试

### 5.1 S4：3 份完整 PDF 四态

| 文件 | 页数 | chunks | 状态轨迹 | 结果 |
|---|---:|---:|---|---|
| `77e4...pdf` | 26 | 62 | PENDING → PARSING → DONE | PASS |
| `9f06...pdf` | 17 | 39 | PENDING → PARSING → DONE | PASS |
| `full3.pdf`（合并 26+17 页） | 43 | 101 | PENDING → PARSING → DONE | PASS |

- 总页数：86；总 chunks：202；Milvus 向量数：202；
- 三份文件均真实调用 MinerU 4，按文件独立输出，无覆盖；
- 证据：`data/s4-accept-20261110-154913/`、`data/s4-upload-result.json`。

### 5.2 真实 Kafka 重复消息回放

对已 `DONE` 的 file 1 重新投递同一条 task payload：

| 指标 | 回放前 | 回放后 |
|---|---:|---:|
| 向量数 | 202 | 202 |
| chunk_count | 62 | 62 |
| 状态 | DONE | DONE |
| Worker 结果 | — | `SKIP_DONE` |

- Worker 日志：`file_id=1 result=SKIP_DONE`；
- 证据：`data/s4-replay-result.json`。

### 5.3 失败态与重新解析

- Worker 使用不可用的 MinerU 后端：文件进入 `FAILED`，`error_code=MINERU_NOT_INSTALLED`；
- DLQ 正确写入 `worker_dlq.jsonl`；
- 切换 mock Worker 后执行重解析：`PENDING → DONE`，3 页/3 chunks；
- 证据：`data/flow-failure-20261110-141638/`。

### 5.4 真实文本/图文 LLM（14/14）

| 检查项 | 结果 |
|---|---|
| 健康检查 / 依赖 | PASS |
| 2 份 PDF 上传/解析/入库 | PASS |
| 文本问答 | PASS，sources=5 |
| 图文问答 | PASS，used_images=4（1 用户图 + 3 检索图） |
| 重复上传 | PASS |

- 文本模型：`qwen3.8-flash`；视觉模型：`qwen3-vl-plus`；
- 证据：`data/llm-final-20261110-154118/`。

直接调用结果（`data/final-answer.json`）：

- 文本问题“产品A销售额在哪个季度下降？”→ 模型明确回答“资料中未提供”，未编造；sources=5；
- 图文问题上传纯白图 → 模型识别为空白图，并基于检索图描述 Agent 认知循环、Copilot 架构、任务验证流程；used_images=4。

### 5.5 本地一键启动/停止

| 步骤 | 结果 |
|---|---|
| Docker 检查 | PASS |
| Kafka 启动/复用 | PASS |
| MinerU 启动/复用 | PASS |
| Milvus Lite server 启动/复用 | PASS |
| 集合检查 | PASS |
| API 启动 + `/health` | PASS |
| Worker 启动 | PASS |

- 冷启动输出 `[1/7]`～`[7/7]`，最终 `System ready: http://127.0.0.1:8000`；
- `/health/deps`：Kafka / Milvus / MinerU 全部 ok；
- 一键停止后 8000 / 19530 / 9092 端口全部释放。

## 6. 缺陷与修复记录

| ID | 严重度 | 问题 | 状态 |
|---|---|---|---|
| D-01 | 高 | MinerU 4.1.7 CLI 与旧 `-p/-o/-b` 不兼容 | 已修复，适配 `parse`/`read` |
| D-02 | 高 | v4 输出固定 `document.md`，多文件互相覆盖 | 已修复，按 PDF stem 分目录 |
| D-03 | 高 | Windows `~/.mineru` 对 Everyone 可写，服务拒绝启动 | 已修复 ACL |
| D-04 | 高 | Worker 先于 API 启动时 `no such table: files` | 已修复，Worker 启动时 `init_db` |
| D-05 | 中 | Worker 日志 `trace_id` 一直为 `[-]` | 已修复，从 payload 注入 |
| D-06 | 高 | `huggingface-hub`/pandas 冲突导致 sentence-transformers 不可用 | 已修复，BGE/CLIP 改用 transformers |
| D-07 | 中 | Chinese-CLIP 新版本返回 `BaseModelOutputWithPooling` | 已修复，提取 `pooler_output` |
| D-08 | 高 | Milvus Lite 本地 URI 被 pymilvus 当 HTTP 解析；集合 `released`；单进程锁 | 已修复，`RAG_MILVUS_URI` + `load_collection` + server 模式 |
| D-09 | 中 | MinerU 子进程输出按 GBK 解码失败 | 已修复，显式 UTF-8 |
| D-10 | 中 | 一键启动脚本被原生 stderr/空退出码/慢端口探测阻塞 | 已修复，超时 + 快速 TcpClient + 步骤进度 |

当前无未关闭的功能性缺陷。

## 7. 已知限制与风险

| ID | 风险 | 影响 | 建议 |
|---|---|---|---|
| R-01 | Docker Milvus standalone 未验证 | 生产集群路径未覆盖 | 镜像可用后补 Milvus 2.4 compose 验收 |
| R-02 | Milvus Lite 为单进程 gRPC，且 `pymilvus 3.0.1` 有 `AllocTimestamp` 噪音 | 不适合多副本/高并发 | 生产换 Docker Milvus 或 Zilliz Cloud |
| R-03 | MinerU 使用远程 `tier=flash` | 依赖外网与 MinerU 服务 | 内网部署时切本地 `pipeline`/自建服务 |
| R-04 | LLM 冒烟使用 2 页样本 | 长文档问答质量未全面评估 | 用 17/26 页完整文档做人工评测 |
| R-05 | 未做压力/并发/长稳/安全渗透 | 容量与安全边界未知 | 上线前补性能、限流、鉴权、防注入测试 |
| R-06 | 前端仅静态资源测试 | 浏览器交互未自动化 | 增加 Playwright/Cypress 端到端用例 |

## 8. 结论与建议

**结论：本轮测试通过，满足当前 S0～S7 与 S4 验收范围。**

建议：

1. 日常本地运行使用 `scripts/start-local.ps1` / `stop-local.ps1`，不要手工开六个终端；
2. 生产部署前补 Docker Milvus 2.4 / Zilliz Cloud 验证；
3. 对 17/26 页完整 PDF 做一次人工问答评测，关注长文档页码引用与图片召回；
4. 增加前端 E2E 与并发压测；
5. 关注 `.env` 中模型名随账号目录变化，当前为 `qwen3.8-flash` / `qwen3-vl-plus`。

## 9. 证据索引

| 证据 | 路径 |
|---|---|
| S4 验收记录 | [acceptance-record.md](acceptance-record.md) |
| 真实环境验收操作单 | [acceptance-runbook.md](acceptance-runbook.md) |
| 本地运行 | [local-run.md](local-run.md) |
| 3 PDF 上传结果 | [s4-upload-result.json](../data/s4-upload-result.json) |
| 重复消息回放结果 | [s4-replay-result.json](../data/s4-replay-result.json) |
| LLM 冒烟运行目录 | `data/llm-final-20261110-154118/` |
| 真实回答 | [final-answer.json](../data/final-answer.json) |
| 失败/重解析现场 | `data/flow-failure-20261110-141638/` |
| 本地启动日志 | `data/.local/logs/` |

---

**测试执行**：AI 编码代理
**报告日期**：2026-11-10
