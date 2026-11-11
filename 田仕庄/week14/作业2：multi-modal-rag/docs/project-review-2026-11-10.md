# 项目全局审计与优化建议

| 项目 | 内容 |
|---|---|
| 审计日期 | 2026-11-10 |
| 审计范围 | 架构、目录、依赖、配置、文档、测试、安全、运维 |
| 当前基线 | 82 tests passed；真实 S4 + 文本/图文 LLM 已通过 |
| 总体结论 | **本地可交付，但仍有 5 个 P0 级问题需要处理后再对外复制/部署** |

## 1. 总体评价

优点：

- 分层清晰：`api`（薄路由）、`ingest`（上传/解析/入库）、`chat`（检索/消息/LLM）边界明确；
- 核心约束执行到位：Kafka 只承载解析、状态经 `transition()` 乐观锁、`flush` 后才 `DONE`、delete-before-insert；
- 真实链路已验证：Kafka → MinerU 4 → BGE/CLIP → Milvus Lite → Qwen；
- 82 个自动化用例覆盖状态机、上传、鉴权、问答、图文、Worker、Sweeper；
- 文档体系较完整：README、验收操作单、S4 记录、测试报告、本地运行分开。

主要问题不是“功能缺失”，而是**交付一致性**：代码、默认配置、依赖清单、Compose、验收文档之间存在漂移。

## 2. P0：对外交付前必须处理

| ID | 问题 | 证据 | 影响 | 建议 |
|---|---|---|---|---|
| P0-1 | 依赖清单与代码不一致 | [requirements.txt](../requirements.txt:12) 仍列 `sentence-transformers`；代码已改用 `transformers` + `torch`；`milvus-lite` 未列 | 新机器按 requirements 安装后可能缺 transformers/torch，或装上不再使用的 datasets 链 | 把依赖拆成 core + optional：`transformers`、`torch` 必装；`milvus-lite` 作为 Lite 可选；移除 sentence-transformers |
| P0-2 | 默认 Compose 不可用，验收文档仍以它为主 | [compose.yaml](../deploy/compose.yaml:48) 使用已下架/被拒的 MinIO 镜像；[验收操作单](acceptance-runbook.md:36) 首选 `up -d kafka milvus` | 新用户照文档执行会卡在镜像拉取；S4 已接受 Lite，但文档主路径仍是 Docker Milvus | 把 Lite server 写成默认本地路径；Docker Milvus 标为“目标部署/后续验证”；或提供 `compose.lite.yaml` |
| P0-3 | 配置默认值与当前模型目录不一致 | [config.py](../app/config.py:134) 默认 `qwen-plus/qwen-vl-plus`、`CLIP_DIM=1024`；[.env.example](../.env.example:42) 为 `qwen3.8-flash/qwen3-vl-plus`、512 | 不通过 `local-env.ps1` 启动时可能 404（模型不存在）或向量维度不匹配 | 默认值跟随当前验收模型；README 明确“按账号可用模型覆盖”；启动时预检模型/维度 |
| P0-4 | OpenAPI 的 `/chat` 没有 requestBody | [openapi.yaml](api/openapi.yaml:334) 只有响应，没有 JSON/multipart 请求体 | 自动生成的客户端/文档不知道如何传 `question`、`images` | 为 `/chat` 增加 `openapi_extra` 或显式请求体 schema；加契约测试 |
| P0-5 | 云端数据外发未在运行文档中明确 | MinerU 当前 `tier=flash` 走 mineru.net；DashScope 会接收文档片段/图片 | 敏感文档被上传到第三方，用户可能不知情 | README/本地运行增加“数据流向与隐私”章节；敏感场景默认本地 MinerU/自建服务 |

## 2.1 P0 处理状态（2026-11-10 已处理）

原始问题保留在下方用于追溯；P0 已落地：

| P0 | 状态 | 变更 |
|---|---|---|
| P0-1 依赖不一致 | ✅ | `requirements.txt` 改为 `transformers + torch`，新增 `requirements-lite.txt`，移除不再使用的 sentence-transformers |
| P0-2 部署主路径不可用 | ✅ | 新增 `deploy/compose.local.yaml`（Kafka only）；启动脚本/验收操作单改为 Lite 默认，Docker Milvus 标为可选目标 |
| P0-3 默认配置漂移 | ✅ | 默认模型改为 `qwen3.8-flash` / `qwen3-vl-plus`，`CLIP_DIM` 默认 512；启动脚本增加 `.env` Key 与模型目录预检 |
| P0-4 `/chat` 契约缺失 | ✅ | 已加 `openapi_extra`（JSON + multipart），新增契约测试；OpenAPI 已重新导出 |
| P0-5 数据外发未说明 | ✅ | README、本地运行文档增加 MinerU/mineru.net 与 DashScope 数据流向说明 |

## 3. 架构审视

### 3.1 做得好的部分

- API 与 Worker 分离，Kafka 只承载解析任务；
- 状态机 + 乐观锁避免并发覆盖；
- `DONE` 与 Milvus `flush` 绑定，避免“完成但检索不到”；
- 重复消息 `SKIP_DONE`，入库 delete-before-insert；
- 检索三路召回 + RRF，`parts` 有用户图/检索图预算；
- trace_id 从 API 到 Worker 已打通。

### 3.2 需要演进的架构点

| 问题 | 影响 | 建议 |
|---|---|---|
| API 和 Worker 各自加载 BGE/CLIP | 内存翻倍；首次问答约 20s、首次入库约 30s | 独立 embedding 服务，或启动时预热 + 进程内共享 |
| Milvus Lite 单进程 gRPC | 不能多副本/高并发；依赖 server 模式 | 生产切 Docker Milvus/Zilliz；Lite 仅本地 |
| 集合维度固定，无 schema 版本 | 换 CLIP 模型需手工重建集合 | 集合元数据记录模型/维度；启动时校验并给出明确错误 |
| MinerU 远程服务 | 网络/配额/隐私风险 | 私有部署 `pipeline` 或自建 vLLM；文档标默认数据流向 |
| SQLite + 单 Worker | 本地够用，多 Worker 有写锁竞争 | 生产换 PostgreSQL；或明确单 Worker 约束 |
| 无速率限制/默认开放鉴权 | 本机可用，暴露到 LAN 有风险 | 生产默认强制 Key；上传/问答限流 |

## 4. 目录结构审视

结构符合 [AGENTS.md](../AGENTS.md) 约束：没有 `backend/`、`src/` 等平行包；`app/api`、`app/ingest`、`app/chat` 分层清楚；`frontend` 无构建；`tests` 按 unit/integration 分离；`docs` 按设计/验收/运行/报告分开。

可优化项：

- `app/__pycache__`、`scripts/__pycache__` 是运行产物，虽被 `.gitignore` 覆盖，交付前可清理；
- `scripts` 同时有 Python 与 PowerShell，命名风格不统一（`start-local.ps1` vs `run_evaluation.py`），建议在 README 的 scripts 表里列清楚用途；
- 没有 Git 仓库：无法追溯“哪次改动引入问题”，也无法做 diff/回滚；按项目规则不主动 `git init`，但交付前应建立版本管理；
- `data/` 含 1.6GB 模型与用户 PDF，虽已忽略，复制项目时容易误带；建议提供 `scripts/download_models.py` 或模型清单。

## 5. 文档一致性

| 位置 | 问题 | 处理 |
|---|---|---|
| [README](../README.md:393) | 配置表默认 `qwen-plus/qwen-vl-plus`，当前账号目录已无这些模型 | 改为当前可用模型或注明“按账号覆盖” |
| [README](../README.md:211) | 示例仍写 `qwen-vl-plus` | 与 `.env` 的 `qwen3-vl-plus` 对齐 |
| [验收操作单](acceptance-runbook.md:36) | 首选 Docker Milvus，但镜像不可用；Lite 是备选 | 把 Lite 设为本地默认，Docker 标为可选目标 |
| [实现计划](implementation-plan.md:156) | S4 行仍以 compose 为主描述 | 注明 S4 验收使用 Lite server，Docker 为后续 |
| [openapi.yaml](api/openapi.yaml:334) | `/chat` 缺请求体 | 见 P0-4 |
| [测试报告](test-report.md) | 结论“无条件通过”可能被误读 | 明确“有条件通过：Lite 替代 + 无压测/安全测试” |

README 底部索引已覆盖本地运行、测试报告、S4 记录、验收操作单、OpenAPI 与 AGENTS，文档入口本身是清楚的。

## 6. 测试审视

现状：82 passed，覆盖状态机、上传、鉴权、问答、图文、Worker、Sweeper、前端静态资源；真实链路有独立冒烟脚本和验收记录。

缺口：

1. **无浏览器 E2E**：前端只测静态资源可访问，未验证上传进度、轮询、问答气泡、图片附件；
2. **无覆盖率门槛**：不知道哪些分支未测；
3. **真实链路未进 CI**：Kafka/MinerU/LLM 冒烟是手工脚本，回归靠人；
4. **测试隔离风险**：[test_parser.py](../tests/unit/test_parser.py:27) 的“非 mock 后端”用例依赖本机 `mineru` 不在 PATH；若 PATH 有 mineru，可能触发真实远程解析；
5. **无压力/长稳/并发/安全测试**；
6. **契约测试缺失**：OpenAPI 与真实 multipart 行为没有自动比对。

建议优先级：

- P1：把真实冒烟拆成 `--skip-chat` 的 CI job（Kafka + MinerU mock/真实可选）；
- P1：补 Playwright 上传/问答/图片附件 E2E；
- P2：覆盖率报告（不强制 100%，设关键模块门槛）；
- P2：契约测试（`/chat` JSON + multipart、错误码）。

## 7. 配置、安全与运维

### 配置

- `.env` 自动加载已实现，shell 环境优先，行为正确；
- `MILVUS_URI` 与 `RAG_MILVUS_URI` 双变量容易混淆：Lite 单进程用后者，server/Docker 用前者，文档已解释但可再简化；
- `start-local.ps1` 未预检 `.env` Key、模型目录、端口占用，失败会推迟到解析/问答阶段；建议启动前 preflight。

### 安全/隐私

- 无 Key 时默认开放读写，适合本机，不适合 LAN；
- API Key 仅 SHA256 存储，无速率限制；
- MinerU 远程 + DashScope 会外发文档/图片，当前文档未突出；
- 上传有扩展名/MIME/魔数/大小校验，图片有 PIL 校验和防解压炸弹；
- 提示注入在系统提示中声明，但未做输出过滤。

### 运维

- 日志轮转 10MB×5，trace_id 完整；
- DLQ 只追加、无轮转/清理；
- `health/deps` 对 MinerU 401 判为 ok（可达但未授权），detail 可读但语义容易误判；
- Milvus Lite/pymilvus 的 `AllocTimestamp` ERROR 噪音会污染 `error.log`；
- 没有 metrics/SLO/告警。

## 8. 建议路线图

### 第一阶段（交付前，1～2 天）

1. 修 requirements：core 依赖 + optional Lite；
2. 统一默认模型/维度，启动时预检模型路径与 `.env`；
3. 把 Lite server 写成默认本地路径，Docker Milvus 标为后续；
4. 补 `/chat` OpenAPI 请求体；
5. 文档增加“数据外发与隐私”说明。

### 第二阶段（稳定性，2～3 天）

1. 测试隔离修复（monkeypatch `shutil.which`）；
2. Playwright E2E；
3. pymilvus/Lite 噪音处理（pin 或过滤）；
4. 启动脚本可移植化（不硬编码 `C:\Users\Cooper`）；
5. DLQ 轮转/告警。

### 第三阶段（生产化）

1. Docker Milvus/Zilliz + PostgreSQL；
2. 独立 embedding 服务/预热；
3. 限流、强制鉴权、监控；
4. 长文档评测集与压测。

## 9. 结论

项目已经达到“本地可演示、可验收”的水平，核心架构不需要重写。真正需要优先处理的是：

```text
依赖可复现  >  部署文档一致  >  配置默认值  >  /chat 契约  >  数据外发说明
```

这五项完成后，再谈 embedding 服务化、E2E、压测和 Docker Milvus 才有意义。
