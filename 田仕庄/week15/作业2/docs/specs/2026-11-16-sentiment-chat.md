# 情感对话服务 规格说明

> 日期：2026-11-16 · 来源：grill-with-docs 共识 · 术语以 [CONTEXT.md](../../CONTEXT.md) 为准

## 1. 问题陈述

课程作业要求交付一个可运行的「情感对话」服务：对用户输入做情感识别，再生成温和、简短的中文回复，并演示 LangGraph 与 OpenAI Agents SDK 两种编排方式。

当前仓库只有 README 设计文档与课程示例脚本，`app/`、`frontend/`、`tests/`、`docker/` 等实现目录全部缺失。本规格把已达成共识的设计固化为可拆任务的行为清单，作为后续 `to-tickets` 与 `implement` 的唯一依据。

> 实现状态（2026-11-16）：`app/`、`frontend/`、`tests/`、`docker/`、`scripts/seed.py`、`alembic.ini`、`pyproject.toml` 已落地，71 项测试通过；Docker Compose 启动与种子幂等已验证。

## 2. 方案概述

分层依赖方向为 `api → services → domain ← engines`：

- `domain.ports.PipelineAdapter` 定义引擎接口；两套引擎各自实现，互不感知。
- `engines/factory.py` 是唯一读取 `PIPELINE_ENGINE` 的地方；非法取值启动即失败，不做静默回退。
- `services` 只依赖 `domain` 接口，负责一次对话的完整流程与业务表读写。
- 业务表 `conversations` / `messages` 是前端历史的**唯一真相来源**；两套**引擎状态**各自存于独立状态表，同库不同表、互不读取。
- 前端为静态页面（`index.html` + `app.js` + `styles.css`），由 FastAPI 挂载，不引入前端框架。
- Alembic 负责业务表；两套引擎状态表由各自 SDK 幂等创建；Docker Compose 编排 api + postgres。
- 交付文档为单文件 `README.md`（含「交付清单与验收标准」），变更历史在 `CHANGELOG.md`，术语在 `CONTEXT.md`，决策记录在 `docs/adr/`。

## 3. 用户故事

### 对话主链路

**US-01 获得情感与回复**
给定服务已启动且 `DASHSCOPE_API_KEY` 有效，当用户 `POST /chat` 发送一条非空文本，则返回 `200`，响应含 `input`、`sentiment`（`positive` 或 `negative`）、`reply`、`conversation_id`；`reply` 为中文、语气温和、内容简短。

**US-02 二分类覆盖**
当输入明显为正向文本时 `sentiment=positive`；明显为负面文本时 `sentiment=negative`。仅此两类，不输出置信度与理由。

**US-03 多轮记忆**
当对同一 `conversation_id` 连续发送两条消息时，第二条的 `reply` 能体现对第一条上文的理解（引用上文信息）；引擎状态按 `conversation_id` 关联。

**US-04 会话自动创建**
当 `conversation_id` 为合法 UUID v4 且后端首次见到时，自动创建会话记录，无需独立建会话接口。

### 历史查询

**US-05 按时间正序恢复历史**
当查询存在的会话时，`GET /conversations/{conversation_id}/messages` 返回 `200`，消息按 `created_at` 正序排列，每条含 `id`（分页游标）；`limit` 默认 50、上限 200；用户消息 `sentiment` 为 `null`，助手消息携带当次识别结果。

**US-06 游标分页**
当请求带上 `before` 时，取值为消息 ID（对前端不透明）；后端按 `(created_at, id)` 定位，返回严格早于该消息的记录。同一时间戳的多条消息不会重复或遗漏。

**US-07 历史查询错误语义**
查询不存在的会话返回 `404` + `CONVERSATION_NOT_FOUND`；会话存在但无消息返回 `200` + 空数组。两者不得混淆。

### 输入契约

**US-08 trim 全链路一致**
用户输入的校验、存储、响应 `input` 回显统一使用 trim 后文本；未 trim 原文不落库、不回显。

**US-09 输入校验**
trim 后为空返回 `422` + `EMPTY_INPUT`；trim 后长度超过 `MAX_INPUT_LENGTH`（默认 4000）返回 `422` + `INPUT_TOO_LONG`；`conversation_id` 非 UUID v4 返回 `422` + `INVALID_CONVERSATION_ID`。

### 失败兜底

**US-10 模型失败透明降级**
模型调用超时、限流或不可用时，接口返回 `503`，错误码分别为 `LLM_TIMEOUT` / `LLM_RATE_LIMITED` / `LLM_UNAVAILABLE`，统一错误体不泄露堆栈与内部路径。

**US-11 解析失败兜底**
模型输出无法规整为两类时，按 `negative` 兜底并生成温和回复，同时日志记录 `parse_failed`，接口仍返回 `200`。

**US-12 健康检查**
`GET /health` 正常返回 `200` + `{"status":"ok","db":"ok","engine":"<当前引擎>"}`；数据库不可达返回 `503` + `"db":"error"`，供 readiness 探针摘除实例。

### 引擎与并发

**US-13 引擎切换**
修改 `PIPELINE_ENGINE=openai-agents` 并重启后，API 契约、业务表结构、前端历史均不变；`/health` 的 `engine` 随之变化；模型上下文重置，且该行为在 README 中作为已知限制声明。

**US-14 非法引擎配置**
当 `PIPELINE_ENGINE` 取非 `langgraph` / `openai-agents` 的值时，应用启动失败并给出明确错误，不静默回退。

**US-15 会话内串行、跨会话并发**
同一 `conversation_id` 的并发请求通过进程内 `asyncio.Lock` 串行处理；不同会话可并发。该保证仅在单进程内有效，多实例局限写入文档。

### 可观测与交付

**US-16 日志合规**
每条请求输出一行 JSON 日志，包含 `conversation_id`、`engine`、`sentiment`、`latency_ms`、`status`（解析失败另含 `parse_failed`）；不记录用户原文，仅记录 `input_length` 与 `input_sha256` 前 8 位。

**US-17 前端可用**
`http://localhost:8000/` 提供输入框、对话气泡与情感徽章；刷新页面后通过历史接口完整恢复对话。

**US-18 容器化启动**
`docker compose --env-file .env -f docker/docker-compose.yml up -d --build` 后 migrate 一次性容器 exited 0、api 与 db 均 healthy；容器内 `DATABASE_URL` 指向 `db:5432`；迁移由独立 migrate 服务执行，api 等待其成功；服务可通过 `http://localhost:8000/` 访问。

**US-19 测试全绿且零真实调用**
`pytest` 全量通过，单元测试不依赖数据库，集成测试需要 PostgreSQL 但 LLM 全部 mock；`DASHSCOPE_API_KEY` 为空时测试仍可运行。

**US-20 明文 Key 清理**
`grep -rn "sk-" docs/` 无命中；`docs/` 示例脚本统一从 `DASHSCOPE_API_KEY` 读取；README 明确要求仓库所有者吊销历史泄露的 Key。

**US-21 种子数据幂等**
`python scripts/seed.py` 在 api 容器内连续执行两次，均输出 2 个会话 / 8 条消息且数据库行数不翻倍；业务表缺失时直接报错并提示先执行 `alembic upgrade head`，脚本不建表。

**US-22 API 访问控制**
Docker Compose 默认只把 API 与 PostgreSQL 端口绑定到 `127.0.0.1`。`API_TOKEN` 留空时接口按本机开放模式运行；设置后必须至少 16 字符，`POST /chat` 与历史接口要求 `X-API-Token` 请求头，缺失或错误返回 `401` + `UNAUTHORIZED`；`GET /health` 始终公开。静态前端不内嵌令牌，公网部署由反向代理认证并注入请求头。

## 4. 已定决策

| # | 决策 | 理由 / 记录 |
| --- | --- | --- |
| D1 | 两套引擎都交付，`PIPELINE_ENGINE` 切换，共用 API 契约与业务表 | 课程要求 |
| D2 | 切换引擎不保留模型上下文，仅在文档声明 | 见 [ADR-0001](../../docs/adr/0001-引擎切换接受上下文重置.md)；重建上下文会侵入 `ChatService` 并改变"业务历史唯一真相"的边界 |
| D3 | 历史分页 `before` 用消息 ID 作不透明游标，内部按 `(created_at, id)` 定位 | 避免时间戳重复导致分页丢失 |
| D4 | `input` 的校验、存储、回显统一用 trim 后文本 | 消除"原样回显"与"已 trim"的二义 |
| D5 | 情感仅二分类，不含置信度、理由与具体情绪 | 作业范围；无法做阈值化风险判定 |
| D6 | 业务表与引擎状态表同库不同表、互不读取；业务表由 Alembic 建表，引擎状态表由各自 SDK 幂等创建 | 前端历史与模型记忆职责分离；状态表结构随 SDK 版本演进，强行纳入 Alembic 会耦合内部 schema |
| D7 | `scripts/seed.py` 只写数据、不建表 | 避免与迁移脚本职责重叠 |
| D8 | 前端为无框架静态页面，由 FastAPI 挂载 | 降低交付复杂度 |
| D9 | 交付文档保持单文件 README，新增验收清单；变更历史独立为 `CHANGELOG.md` | 便于单文件评审 |
| D10 | `docs/` 示例脚本改为环境变量注入；泄露 Key 由所有者吊销 | 安全整改，见 README 安全章节 |
| D11 | 端口默认绑定回环；`API_TOKEN` 可选启用，未配置时保持本机开放 | 不破坏课程演示；公网部署必须显式启用共享令牌与反向代理认证 |
| D12 | 运行时直接依赖精确固定，Docker 基础镜像使用 digest | 避免构建漂移；升级需显式更新并跑全量测试 |
| D13 | 迁移由 Compose 一次性 `migrate` 服务执行，`api` 等待其成功 | 避免多实例并发迁移；`entrypoint.sh` 只负责 `exec` |

## 5. 测试决策

### 接缝（seam）

按从高到低排列，优先使用最高接缝：

1. **HTTP API 接缝**（主接缝）：`POST /chat`、`GET /conversations/{id}/messages`、`GET /health`。集成测试在此层驱动，使用真实 PostgreSQL（优先 `TEST_DATABASE_URL`，否则 testcontainers）与假 LLM，不模拟 `PipelineAdapter`——两套引擎的真实编排代码都要被执行。
2. **LLM 客户端边界**：在 `engines/shared/llm.py` 的对外调用处注入假实现（固定输出 / 抛超时与限流异常），断言引擎对输出的解析与错误映射。
3. **鉴权中间接缝**：`API_TOKEN` 配置校验与 `require_api_token` 依赖；集成测试断言缺失 / 错误令牌返回 401，正确令牌放行，`/health` 不受影响。
4. **单元接缝**：`parser` 的规整函数、`prompts` 拼装、`Settings` 校验（非法 `PIPELINE_ENGINE` 启动失败、短 `API_TOKEN` 启动失败）、trim 与长度校验。均为纯函数，不依赖数据库与网络。

### 各层覆盖

| 层级 | 覆盖内容 | 外部依赖 |
| --- | --- | --- |
| `tests/unit` | parser 规整、prompts 拼装、枚举与领域对象、Settings 校验（含 `API_TOKEN` 归一化与长度） | 无 |
| `tests/integration` | `/chat` 的 200 / 422 / 503 / 401 分支、两套引擎路由与持久化、历史查询与游标、404 语义、静态前端挂载、`X-API-Token` 校验 | PostgreSQL；LLM mock |

### "完成"的定义

1. README「交付清单与验收标准」第 1–15 项全部可执行并通过；
2. `pytest` 全绿，且未调用真实模型；
3. 两套引擎各完成一次手工演示（同一 `conversation_id` 多轮 + 历史恢复 + 切换重启）；
4. `grep -rn "sk-" docs/` 无命中。

## 6. 明确不做

- **多用户隔离、用户级授权与限流**：只提供可选共享 `API_TOKEN` 与回环端口隔离；`conversation_id` 仍不构成身份凭证，不区分用户；本作业不实现（README 已标注生产必补）。
- **内容审核与危机干预**：不做敏感词检测、危机识别、人工介入、未成年人保护；仅保留免责声明与温和话术。
- **流式输出**：`docs/05_流式输出.py` 仅作参考，不纳入 API 与前端。
- **多实例分布式锁**：不做 PostgreSQL advisory lock 或 Redis 锁；单实例串行化即为交付范围。
- **引擎状态迁移 / 上下文重建**：见 D2 与 ADR-0001。
- **情感细分、置信度、风险阈值**：仅二分类。
- **前端框架与复杂交互**：不做构建链、路由、状态管理。
- **`docs/` 脚本重命名与编号整理**：README 中的整理建议仅记录，不在本次交付执行。
- **Git 历史清理与开源许可**：仓库当前非 Git 仓库；不选许可证，不清理历史。

## 7. 补充说明

- **术语一致性**：会话、业务历史、引擎状态、引擎、PipelineAdapter、情感、游标、输入文本以 `CONTEXT.md` 为准；README 不再用「管线」指代引擎。
- **安全边界**：默认仅绑回环；`API_TOKEN` 是可选共享密钥，不区分用户。`conversation_id` 可被枚举读取历史，前端生成 UUID v4 只是抗猜，不构成鉴权。
- **文档拆分时机**：README 的「架构」「API」章节超过约 150 行时再拆到 `docs/architecture.md` / `docs/api.md`，本次保持单文件。
- **Key 处置状态**：脚本中的明文 Key 已移除，但 4 个历史泄露 Key 仍须由仓库所有者在百炼控制台吊销；这是唯一无法由代码完成的安全动作。
- **环境约束**：Python 3.12、PostgreSQL 16、Compose v2；端口 8000 / 5432 冲突时需同步调整 compose 映射与 `DATABASE_URL`。
