# Sentiment Chat

> **状态：全部交付物已实现（All Deliverables Implemented）** — `app/`、`frontend/`、`tests/`、`docker/`、`scripts/seed.py`、`alembic.ini`、`pyproject.toml` 已落地，71 项单元 + 集成测试在 PostgreSQL 16 上全绿。Docker Compose 的 migrate 一次性迁移、api + db 健康检查、前端首页与种子脚本幂等性均已验证；本地启动后可直接访问 `http://localhost:8000/`。
>
> **最后更新**：2026-11-16 · **语言** Python 3.12 · **框架** FastAPI · **存储** PostgreSQL 16

基于 **FastAPI + Qwen** 的情感对话服务。用户输入文本，系统先做**正向 / 负面**情感识别，再根据结果生成**温和、简短的中文**鼓励或排解回复，支持多轮对话与上下文记忆。

项目提供**两套可切换的编排引擎**：

- **LangGraph** — 显式两节点图 + 条件边，使用 `AsyncPostgresSaver` 持久化图状态。
- **OpenAI Agents SDK** — 两个 Agent 顺序协作，使用 `SQLAlchemySession` 持久化会话。

两套引擎**共用同一个前端、同一套 API 契约、同一个 PostgreSQL 数据库**，仅通过环境变量 `PIPELINE_ENGINE` 切换。

**想直接跑起来** → [快速开始](#快速开始)。**想了解设计取舍** → [架构](#架构)。

---

## 功能特性

- **情感识别**：仅 `positive` / `negative` 二分类，不输出置信度与理由；结果随响应直接返回，前端渲染徽章。
- **回复生成**：温和、简短的中文鼓励或排解话术。
- **多轮对话**：按 `conversation_id` 维持上下文；历史可查询，刷新页面后恢复。
- **双引擎切换**：`PIPELINE_ENGINE=langgraph|openai-agents`，共用 API 契约与数据库。
- **持久化**：PostgreSQL 存业务会话、消息与两套引擎各自的状态。
- **并发安全**：异步全链路，同一会话串行、跨会话并发（单实例内有效）。
- **可观测与容错**：JSON 日志不记原文；空 / 超长输入、模型超时 / 限流、解析失败均有兜底。
- **交付形态**：Docker Compose 一键启动；单元 + 集成测试全部 mock LLM。

---

## 交付清单与验收标准

每一项都对应一条可执行验证，交付前逐项勾选；命令中的端口与 `conversation_id` 以本地实际为准。

| # | 验收项 | 验证方式 | 预期 |
| --- | --- | --- | --- |
| 1 | 服务可启动 | `docker compose --env-file .env -f docker/docker-compose.yml up -d --build` | migrate 一次性容器 exited 0；api 与 db 容器均 healthy |
| 2 | 健康检查 | `curl -s localhost:8000/health` | `200`，`db=ok`，`engine` 与 `PIPELINE_ENGINE` 一致 |
| 3 | 情感识别 + 回复 | `POST /chat` 发送负面示例 | `200`，`sentiment=negative`，`reply` 为中文温和话术 |
| 4 | 二分类覆盖 | 分别发送正向 / 负面文本 | `sentiment` 分别为 `positive` / `negative` |
| 5 | 多轮记忆 | 同一 `conversation_id` 连发两条 | 第二条回复引用上文 |
| 6 | 历史恢复 | `GET /conversations/{id}/messages` | 按时间正序返回 user / assistant 消息 |
| 7 | 404 语义 | 查询不存在的会话 | `404` + `CONVERSATION_NOT_FOUND` |
| 8 | 输入校验 | 空输入 / 超长输入 | `422` + `EMPTY_INPUT` / `INPUT_TOO_LONG` |
| 9 | 引擎切换 | 改 `PIPELINE_ENGINE=openai-agents` 后重启 | `/health` 中 `engine` 变化；历史仍在；模型上下文重置（见 [引擎切换的真实影响](#引擎切换的真实影响)） |
| 10 | 错误兜底 | 集成测试覆盖模型超时 / 限流 | 返回 `503` + 对应错误码 |
| 11 | 测试全绿 | `pytest` | 全部通过，且未调用真实模型 |
| 12 | 日志合规 | 查看 stdout JSON 日志 | 含 `conversation_id` / `engine` / `sentiment` / `latency_ms` / `status`，不含用户原文 |
| 13 | 明文 Key 清理 | `grep -rn "sk-" docs/` | 无命中；旧 Key 已在控制台吊销 |
| 14 | 前端可用 | 打开 `http://localhost:8000/` 发送消息并刷新页面 | 显示用户 / 助手气泡与情感徽章；刷新后历史自动恢复；新对话生成新 ID |
| 15 | 种子数据幂等 | 在 api 容器连续执行两次 `python scripts/seed.py` | 两次均为 2 个会话 / 8 条消息，不重复写入；缺表时提示先执行迁移 |

> 第 2–12 项的核心路径已由 `pytest`（TestClient + PostgreSQL 16 + mock LLM）覆盖；第 14 项已用真实浏览器（LangGraph 引擎）验证；第 1、15 项已在 Docker Compose（PostgreSQL 16）上验证；第 13 项现在即可验证。

---

## 安全与免责声明

> **本服务不构成心理援助或医疗建议。**
> 情感陪伴场景可能接收到极端负面内容；系统仅做二分类与温和话术引导，不具备危机识别、干预或转介能力。面向真实用户开放前，必须补充危机干预提示、求助热线入口、人工介入通道、内容审核与未成年人保护策略。

**数据与密钥**

- 用户消息原文持久化在 `messages` 表以支持回放，属敏感数据；需明确留存期限、提供删除入口，并考虑静态加密。
- 日志**不记录**用户原文，仅记长度与哈希；`.env` 不入库，CI/CD 用 secrets 注入，生产建议使用 RAM 子账号临时凭证。
- **安全警告**：`docs/` 下 6 个示例脚本曾硬编码 4 个真实 DashScope API Key，现已改为从 `DASHSCOPE_API_KEY` 读取；**泄露的 Key 仍须立即在百炼控制台吊销并重新签发**，详见 [文档索引](#文档索引)。

---

## 前置条件

| 依赖 | 版本要求 | 用途 |
| --- | --- | --- |
| Python | 3.12（3.11 未验证） | 运行时 |
| PostgreSQL | 16 及以上 | 业务表 + 两套引擎状态表（`AsyncPostgresSaver` 依赖 PG 的存储能力） |
| Docker + Compose | Compose v2（`docker compose` 子命令） | 拉起数据库与一键部署 |
| DashScope 账号 | 已开通百炼服务并创建 API Key | 调用 `qwen-flash`，**按量计费** |

可选 `conda` / `curl` / HTTPie。默认只占用宿主回环 `127.0.0.1` 的 `8000`（API）与 `5432`（PG），不会暴露到局域网；端口冲突时需同时调整 compose 映射与 `DATABASE_URL`。

---

## 快速开始

### 方式一：本地 conda（Python 3.12）

```bash
conda create -n sentiment-chat python=3.12 -y && conda activate sentiment-chat
pip install -e .                    # 可编辑安装
cp .env.example .env                # 填入 DASHSCOPE_API_KEY；DATABASE_URL 密码须与 DB_PASSWORD 一致
docker compose --env-file .env -f docker/docker-compose.yml up -d db
alembic upgrade head                # 创建业务表（引擎状态表由 SDK 启动时幂等创建）
python -m app.main
```

> Windows 必须用 `python -m app.main`：内部固定 SelectorEventLoop。直接 `uvicorn app.main:app` 会使用 ProactorEventLoop，psycopg 异步模式无法运行，启动会停在 `Waiting for application startup` 或直接报错；`--reload` 的子进程也会退回 Proactor。
>
> `DATABASE_URL` 的主机用 `127.0.0.1`：`localhost` 会先解析到 IPv6 `::1`，Docker 只监听 IPv4，连接需等超时才回落。密码须与 `DB_PASSWORD` 一致，否则无法连接。

打开 `http://localhost:8000/` 访问前端页面；`http://localhost:8000/docs` 是 FastAPI 自动生成的 Swagger UI，`/openapi.json` 是 OpenAPI 描述。

### 方式二：Docker Compose

```bash
cp .env.example .env
# 编辑 .env，至少填入 DASHSCOPE_API_KEY 与 DB_PASSWORD

docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose --env-file .env -f docker/docker-compose.yml ps -a   # migrate 应为 exited 0，api 与 db 变为 healthy
docker compose --env-file .env -f docker/docker-compose.yml exec api python scripts/seed.py
```

打开 `http://localhost:8000/`。种子脚本只写业务数据、不建表，可重复执行；Compose 先运行一次性 `migrate` 服务执行 `alembic upgrade head`，`api` 等待其成功后再启动。Linux 容器内 Uvicorn 使用 uvloop；本地 Windows 请用 `python -m app.main` 固定 SelectorEventLoop。

> `docker/` 作为 compose 文件目录会使 Compose 的项目目录变成 `docker/`，因此从仓库根执行时必须带 `--env-file .env`，否则根目录 `.env` 不会被读取。
>
> 容器内 API 不能访问 `localhost` 数据库：compose 注入 `DATABASE_URL=postgresql+asyncpg://app:${DB_PASSWORD}@db:5432/sentiment_chat`，手写 `DATABASE_URL` 时主机名须为 `db`。
>
> **访问控制**：compose 默认把 `8000` / `5432` 绑定到 `127.0.0.1`。如需跨机访问，必须在反向代理层启用 TLS 与认证，并在 `.env` 设置至少 16 字符的 `API_TOKEN`；启用后 `/chat` 与历史接口要求 `X-API-Token` 请求头，`/health` 保持公开。静态前端不会内嵌令牌，公网部署应由反向代理注入请求头或另行实现登录会话。

### 启动验证

```bash
curl -s http://localhost:8000/health   # {"status":"ok","db":"ok","engine":"langgraph"}；db 异常返回 503
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"conversation_id":"3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c","message":"今天面试又没过，感觉好失败。"}'
```

若设置了 `API_TOKEN`，`/chat` 与历史接口需额外携带 `-H "X-API-Token: <token>"`；`/health` 始终公开。

复用同一 `conversation_id` 再发一条验证多轮；完整验收项见 [交付清单](#交付清单与验收标准)。

### 停止与清理

本地方式启动的 `uvicorn` 用 `Ctrl+C` 停止。Docker 方式：

```bash
# 停止并移除容器与网络，保留数据库卷（会话历史仍在）
docker compose --env-file .env -f docker/docker-compose.yml down

# 同时删除数据库卷，清空所有会话历史与引擎状态（谨慎）
docker compose --env-file .env -f docker/docker-compose.yml down -v
```

`down` 默认保留 `sentiment-chat_pgdata` 卷，下次 `up` 可直接恢复历史；`down -v` 不可撤销，确认不再需要数据时再执行。

### 常见问题排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `ConnectionRefusedError` / `connect call failed` | 数据库未启动，或 `DATABASE_URL` 仍指向 `localhost` | `docker compose --env-file .env -f docker/docker-compose.yml ps` 确认 db healthy；容器内改用 `@db:5432` |
| `alembic.util.exc.CommandError` | 迁移未执行，或 `alembic.ini` 的 `script_location` 配错 | 在项目根目录执行 `alembic upgrade head` |
| `401` / `InvalidApiKey` | `DASHSCOPE_API_KEY` 缺失或已失效 | 确认 `.env` 被加载：`python -c "import os;print(bool(os.getenv('DASHSCOPE_API_KEY')))"` |
| 接口持续返回 `503` | 模型超时或限流 | 查日志 `status=503`；适当调大 `LLM_TIMEOUT_SECONDS` |
| `sentiment` 恒为 `negative` | 模型输出无法解析，走了兜底 | 查日志 `parse_failed`；核对提示词与模型返回格式 |
| `Address already in use` | 8000 / 5432 被占用 | 换端口或停掉旧进程 / 旧容器 |
| `Psycopg cannot use the 'ProactorEventLoop'` | Windows 下 psycopg 异步限制；uvicorn 0.36+ 在非 reload 模式默认选 Proactor | `python -m app.main` 已用 `loop="none"` 沿用 Selector 策略；直接跑 uvicorn 时加 `--loop none`，或保留 `--reload` |

---

## 配置：环境变量

所有变量通过 `.env` 或进程环境注入，由 `app/config.py` 的 `Settings`（pydantic-settings）统一加载与校验。

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PIPELINE_ENGINE` | 否 | `langgraph` | 引擎选择：`langgraph` 或 `openai-agents`。**取值非法时启动即失败**（fail fast，不做静默回退） |
| `DASHSCOPE_API_KEY` | **是** | — | 阿里云百炼 / DashScope API Key |
| `QWEN_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Qwen 的 OpenAI 兼容端点 |
| `QWEN_MODEL` | 否 | `qwen-flash` | 模型名称 |
| `DATABASE_URL` | 本地**是** | `postgresql+asyncpg://app:app@localhost:5432/sentiment_chat` | 异步 PostgreSQL 连接串。Docker 部署时由 compose 注入，主机名必须是 `db` |
| `DB_PASSWORD` | Docker 下**是** | — | compose 用它初始化 PG 密码并拼进 api 的 `DATABASE_URL`；两条路径必须一致，且使用 URL-safe 字符（字母、数字、`-`、`_`） |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `MAX_INPUT_LENGTH` | 否 | `4000` | 单条输入最大字符数 |
| `LLM_TIMEOUT_SECONDS` | 否 | `30` | 单次模型调用超时 |
| `API_TOKEN` | 否 | 空 | 至少 16 字符；设置后 `/chat` 与历史接口要求 `X-API-Token` 头。留空时仅依赖回环端口隔离 |

表内默认值仅表示 `.env` 缺省时的兜底；本地方式必须显式提供 `DATABASE_URL`，Docker 方式必须提供 `DB_PASSWORD`。优先级：进程环境变量 > `.env` > 代码默认值。

`.env.example` 内容（占位符，不可填入真实值）：

```ini
# 大模型（三套 LLM 客户端共用）
DASHSCOPE_API_KEY=sk-your-key-here
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-flash
LLM_TIMEOUT_SECONDS=30
MAX_INPUT_LENGTH=4000

PIPELINE_ENGINE=langgraph

DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/sentiment_chat
# Docker 密码须使用 URL-safe 字符
DB_PASSWORD=change-me

# 可选访问令牌；设置后 /chat 与历史接口要求 X-API-Token，至少 16 字符
API_TOKEN=

LOG_LEVEL=INFO
```

---

## API

请求 / 响应均为 `application/json; charset=utf-8`。完整交互式文档见 `/docs`。

### POST /chat

发送一条用户消息，返回情感识别结果与回复。

**请求字段**

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `conversation_id` | string | 是 | 36 字符 UUID v4 格式 | 由前端生成。后端首次见到新 ID 时自动创建会话记录 |
| `message` | string | 是 | trim 后长度 1 ~ `MAX_INPUT_LENGTH` | 用户输入文本 |

示例请求体见 [启动验证](#启动验证)。

**响应 200**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `input` | string | trim 后的用户输入；校验、存储、回显统一使用该文本 |
| `sentiment` | `"positive"` \| `"negative"` | 情感识别结果 |
| `reply` | string | 模型生成的鼓励 / 排解话术 |
| `conversation_id` | string | 回显会话 ID，便于前端校验 |

```json
{
  "input": "今天面试又没过，感觉好失败。",
  "sentiment": "negative",
  "reply": "面试失利确实让人沮丧，但这只是一次经历，不代表你的能力。先允许自己难过一会儿，再复盘一下可以改进的地方，你会走得更稳。",
  "conversation_id": "3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c"
}
```

### GET /conversations/{conversation_id}/messages

按时间正序返回该会话的历史消息，供前端刷新后恢复对话。

| 参数 | 位置 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | — | 会话 ID |
| `limit` | query | 否 | `50` | 返回条数，上限 `200` |
| `before` | query | 否 | — | 不透明游标，取值为消息 ID；后端按 `(created_at, id)` 定位，返回该消息之前的记录 |

响应 200 时 `messages[]` 元素字段：`id`（消息 ID，分页游标）、`role`（user / assistant）、`content`、`sentiment`（用户消息为 `null`）、`created_at`（UTC，ISO 8601 带 `Z`）。会话存在但无消息返回 `200` + 空数组；会话不存在返回 `404`（统一错误体），不返回空列表。

> **安全提示**：`conversation_id` 由前端生成，本身不构成身份凭证。默认 Compose 仅绑定回环；设置 `API_TOKEN` 后接口要求 `X-API-Token`，但仍是共享密钥，不区分用户。生产环境还必须叠加用户身份、会话归属校验（或对 ID 签名）与限流，否则持有 ID/令牌的人仍可读取完整历史。

### GET /health

供容器与编排系统健康检查使用。

```json
{"status": "ok", "db": "ok", "engine": "langgraph"}
```

- 正常：`200`
- 数据库不可达：`503`，`"db": "error"`（用于 readiness 探针摘除实例）

### 错误响应

统一错误体 `{"error": {"code": "...", "message": "..."}}`，不泄露堆栈与内部路径。

| 状态码 | `code` 示例 | 场景 |
| --- | --- | --- |
| 404 | `CONVERSATION_NOT_FOUND` | 查询不存在的会话历史 |
| 422 | `EMPTY_INPUT` / `INPUT_TOO_LONG` / `INVALID_CONVERSATION_ID` | Pydantic 层校验失败 |
| 422 | `INVALID_CURSOR` | `before` 指向的消息不存在或不属于该会话 |
| 422 | `VALIDATION_ERROR` | 其他参数校验失败（如 `limit` 越界） |
| 503 | `LLM_TIMEOUT` / `LLM_RATE_LIMITED` / `LLM_UNAVAILABLE` | 模型超时、限流、不可用 |
| 500 | `INTERNAL_ERROR` | 未预期异常，仅返回通用文案 |

---

## 前端

`frontend/` 是无构建链的静态页面，由 FastAPI 在 `/` 同源挂载（`index.html` + `app.js` + `styles.css`）。

- 首次访问生成 UUID v4 并存入 `localStorage`，后续消息复用同一 `conversation_id`。
- 发送消息调用 `POST /chat`，展示用户 / 助手气泡与情感徽章；失败时回填输入并显示错误码对应的中文提示。
- 刷新页面调用历史接口恢复最近 50 条消息；「新对话」清除本地会话 ID 并生成新的 UUID。
- 页面通过 `/health` 展示当前引擎与服务状态；不引入前端框架与构建工具。

---

## 测试

```bash
pytest                          # 全量（mock LLM，不消耗真实 API）
pytest tests/unit               # 纯单元测试，不需要数据库
pytest tests/integration        # 集成测试，需要 PostgreSQL
pytest --cov=app --cov-report=term-missing
```

| 层级 | 覆盖内容 | 外部依赖 |
| --- | --- | --- |
| `tests/unit` | `parser` 输出规整、`prompts` 拼装、枚举与领域对象 | 无 |
| `tests/integration` | `/chat` 的 200 / 422 / 503 分支、两套 `PipelineAdapter` 路由与持久化、历史查询接口、静态前端挂载与资源响应 | PostgreSQL；LLM 全部 mock |

- 集成测试数据库：优先 `TEST_DATABASE_URL`，否则 testcontainers 拉起临时 PG（需本机 Docker）；真实模型永不调用，`DASHSCOPE_API_KEY` 可为空。
- CI 建议：GitHub Actions 起 `postgres:16` → `alembic upgrade head` → `pytest`。

---

## 架构

### 分层与依赖方向

`api → services → domain ← engines`

- `engines` 实现 `domain.ports.PipelineAdapter`；
- `services` 只依赖 `domain` 接口，不认识任何具体框架；
- `engines/factory.py` 是**唯一**知道当前引擎的地方。

```mermaid
flowchart TD
    FE["Web 前端（静态页面）<br/>输入框 · 气泡 · 情感徽章"] -->|"POST /chat"| API["FastAPI（异步）<br/>路由 · 校验 · 日志 · 异常处理"]
    API --> SVC["services<br/>ChatService / ConversationService"]
    SVC --> PORT{{"domain.ports<br/>PipelineAdapter"}}
    PORT --> LG["LangGraph 引擎<br/>两节点图 + 条件边"]
    PORT --> OA["OpenAI Agents 引擎<br/>SentimentAgent → ReplyAgent"]
    LG --> SH["engines/shared<br/>LLM 客户端 · 提示词 · 输出解析"]
    OA --> SH
    SVC --> BIZ[("conversations / messages<br/>业务表")]
    LG --> CKPT[("checkpointer 状态表")]
    OA --> SESS[("SQLAlchemySession 状态表")]
```

### 双引擎对比

| 维度 | LangGraph | OpenAI Agents SDK |
| --- | --- | --- |
| 编排形态 | 显式图：识别节点 → 条件边 → 鼓励 / 排解节点 | 两个 Agent 顺序调用 |
| 中间状态 | 图状态（TypedDict）显式可查 | 由 Agent 上下文内部维护 |
| 多轮记忆 | `AsyncPostgresSaver`，`thread_id = conversation_id` | `SQLAlchemySession`，`session_id = conversation_id` |
| 可观测性 | 可 `get_state_history` 回放（见 `docs/04_回放.py`） | 依赖 SDK 事件流 |
| 适用场景 | 需要分支、回放、中断续跑 | 需要快速组合多个 Agent、交接（handoff） |

### 引擎切换的真实影响

切换只需改 `.env` 并重启，API 契约与业务表结构不变。**前端历史保留**（`messages` 表照常恢复），但**模型多轮上下文丢失**：两套引擎状态表互不读取，模型从「无上文」重新开始。本作业接受该行为，记为已知限制，不做重建或迁移（见 [ADR-0001](docs/adr/0001-引擎切换接受上下文重置.md)）。

### 为什么有三套 LLM 客户端

框架约束所致，三者指向同一 `base_url` 与同一模型：

- `engines/shared/llm.py` 用 `AsyncOpenAI`：引擎无关的底层调用与解析重试；
- `engines/langgraph/graph.py` 用 `ChatOpenAI`：图节点需要 LangChain `Runnable` 接口；
- `engines/openai_agents/pipeline.py` 用 `OpenAIChatCompletionsModel` 包装 `AsyncOpenAI`：Agents SDK 默认走 Responses API，Qwen 兼容端仅支持 Chat Completions，并需 `set_tracing_disabled(True)` 关闭 tracing 上报。

### 持久化：三类表

| 表 | 写入方 | 职责 |
| --- | --- | --- |
| `conversations` / `messages` | 两套引擎都写 | 前端历史展示的**唯一真相来源** |
| LangGraph checkpointer 表 | 仅 LangGraph | 只服务图状态延续 |
| OpenAI Agents session 表 | 仅 Agents | 只服务 Agent 会话延续 |

三类表**同库不同表，互不读取**。业务表由 Alembic 创建与变更；LangGraph checkpointer 表在 `AsyncPostgresSaver.setup()` 时、Agents session 表在首次使用时由 SDK 幂等创建。

多轮流程：前端复用同一 `conversation_id` 连续请求 → 引擎按该 ID 加载历史 → 生成回复 → 同时写业务表与引擎状态表。

### 并发模型与日志

- 全程 `async def`，`AsyncOpenAI` 与 `asyncpg` 不阻塞事件循环；跨会话并发由连接池承载。
- **同一 `conversation_id` 通过进程内 `asyncio.Lock` 串行化**，避免记忆交错；锁仅在单进程内有效（多实例限制见 [已知限制](#已知限制)）。
- 日志为 JSON 行，字段见 [交付清单](#交付清单与验收标准)；**不记录用户原文**，仅记 `input_length` 与 `input_sha256` 前 8 位，原文只存在于业务库。

---

## 目录结构

```text
sentiment-chat/
├── app/
│   ├── main.py                     # FastAPI 入口：lifespan 装配引擎、DB、日志
│   ├── config.py                   # 环境变量与 Settings
│   │
│   ├── domain/                     # 纯领域层：不依赖 FastAPI / LangGraph / Agents
│   │   ├── enums.py                # Sentiment 枚举（positive / negative）
│   │   ├── models.py               # PipelineResult 等领域对象
│   │   ├── errors.py               # AppError 与契约错误码
│   │   └── ports.py                # PipelineAdapter 抽象接口
│   │
│   ├── engines/                    # 两套引擎实现，互相隔离
│   │   ├── factory.py              # 按 PIPELINE_ENGINE 返回具体 PipelineAdapter
│   │   ├── shared/                 # 共用：LLM 客户端、提示词、输出解析
│   │   │   ├── llm.py              # AsyncOpenAI + qwen-flash 配置
│   │   │   ├── prompts.py          # 情感识别 / 鼓励 / 排解 三套提示词
│   │   │   └── parser.py           # 将模型输出规整为 positive / negative
│   │   ├── langgraph/
│   │   │   ├── pipeline.py         # 实现 PipelineAdapter
│   │   │   ├── graph.py            # 两节点图 + 条件边
│   │   │   └── checkpointer.py     # AsyncPostgresSaver 生命周期
│   │   └── openai_agents/
│   │       ├── pipeline.py         # 实现 PipelineAdapter
│   │       ├── agents.py           # SentimentAgent / ReplyAgent
│   │       └── session.py          # SQLAlchemySession 生命周期
│   │
│   ├── services/                   # 应用服务：编排引擎 + 业务表读写
│   │   ├── chat_service.py         # 一次对话的完整流程
│   │   ├── conversation_service.py # 会话创建、历史查询
│   │   └── locks.py                # 同一会话的进程内串行锁
│   │
│   ├── api/                        # HTTP 层，只做协议转换
│   │   ├── deps.py                 # 注入 pipeline / db / service
│   │   ├── errors.py               # 统一错误体与异常处理器
│   │   └── routes/
│   │       ├── chat.py             # POST /chat
│   │       ├── conversation.py     # GET /conversations/{id}/messages
│   │       └── health.py           # GET /health
│   │
│   ├── schemas/                    # Pydantic 请求 / 响应契约
│   │   ├── chat.py                 # ChatRequest / ChatResponse
│   │   └── conversation.py         # MessageItem / MessageListResponse
│   │
│   └── infra/                      # 基础设施，不含业务逻辑
│       ├── db/
│       │   ├── models.py           # Conversation / Message 业务表
│       │   ├── session.py          # AsyncSession 工厂、连接池
│       │   └── migrations/         # env.py、script.py.mako、versions/
│       └── logging.py              # 结构化 JSON 日志配置
│
├── frontend/                       # 与后端解耦的静态前端，由 FastAPI 挂载
│   ├── index.html
│   ├── app.js
│   └── styles.css
│
├── tests/
│   ├── unit/
│   │   ├── test_settings.py        # 默认值、引擎枚举、API Key 必填
│   │   ├── test_enums.py           # Sentiment 取值与非法值
│   │   ├── test_parser.py          # positive 识别与 negative 兜底
│   │   ├── test_prompts.py         # 提示词拼装
│   │   └── test_schemas.py         # trim、空输入、UUID、序列化
│   └── integration/
│       ├── conftest.py             # 测试 DB、假 LLM、client fixture
│       ├── test_chat_api.py        # 200 / 422 / 503 与多轮
│       ├── test_history_api.py     # 正序、游标、limit、404
│       ├── test_health_api.py      # /health 双引擎
│       └── test_seed.py            # 种子写入与幂等
│
├── docker/
│   ├── Dockerfile                  # 非 root 运行时镜像 + /health 健康检查
│   ├── entrypoint.sh               # 仅 exec CMD；迁移由 compose 的一次性 migrate 服务负责
│   └── docker-compose.yml          # migrate + api + postgres
│
├── docs/                           # 课程示例代码，见「文档索引」
│   ├── 01_graph_api基础.py
│   ├── 02_funciton_api基础.py
│   ├── 03_基础使用.py
│   ├── 03_持久化.py
│   ├── 04_Session缓存.py
│   ├── 04_回放.py
│   ├── 05_子图调用.py
│   ├── 05_流式输出.py
│   ├── 07_LangGraph-Agent.py
│   ├── adr/                        # 架构决策记录
│   │   └── 0001-引擎切换接受上下文重置.md
│   └── specs/                      # 规格说明书
│       └── 2026-11-16-sentiment-chat.md
│
├── scripts/
│   └── seed.py                     # 种子数据（不负责建表）
│
├── alembic.ini                     # Alembic 配置（script_location → app/infra/db/migrations）
├── .dockerignore                   # 排除 .env、测试、文档与缓存
├── .gitattributes                  # shell 脚本与 Dockerfile 强制 LF
├── .env.example
├── .gitignore                      # 必须包含 .env、__pycache__、.pytest_cache
├── pyproject.toml
├── AGENTS.md                       # 编码代理协作约定
├── CHANGELOG.md                    # 文档与设计变更历史
├── CONTEXT.md                      # 术语表
└── README.md
```

> 当前实际状态：`app/`、`frontend/`、`tests/`、`docker/`、`scripts/seed.py`、`alembic.ini`、`pyproject.toml` 与文档均已落地。

---

## 已知限制

- **情感仅二分类**：不识别具体情绪、不输出置信度，无法做阈值化风险判定；回复由 LLM 生成，措辞存在随机性。
- **并发锁仅单实例有效**：同一会话串行化以牺牲单会话并发换取记忆一致性；多实例需外部锁。
- **切换引擎丢失模型上下文**：业务历史保留，引擎状态不互通，见 [ADR-0001](docs/adr/0001-引擎切换接受上下文重置.md)。
- **仅可选共享令牌、无用户隔离与内容审核**：默认仅绑回环；`API_TOKEN` 只提供共享密钥校验，不区分用户。`conversation_id` 仍不构成身份凭证；未接入敏感词检测，开放公网前必须补齐，详见 [安全与免责声明](#安全与免责声明)。

---

## 部署与运维

- **迁移**：本地执行 `alembic upgrade head`；Compose 由一次性 `migrate` 服务执行同一命令，`api` 通过 `service_completed_successfully` 等待其成功，避免多实例启动时并发迁移。引擎状态表由各 SDK 幂等创建（`setup()` / `create_tables`）。`seed.py` 只写数据不建表。业务表回滚用 `alembic downgrade -1`；引擎状态表不受 Alembic 管理，回滚迁移不会删除历史会话的模型记忆。
- **依赖锁定**：Python 直接依赖在 `pyproject.toml` 中精确固定，Docker 基础镜像使用 digest；升级依赖或 Python 补丁时需同步更新并跑全量测试。PostgreSQL 镜像可用 `POSTGRES_IMAGE=postgres:16@sha256:<digest>` 覆盖，生产与 CI 建议固定 digest。
- **种子数据**：`docker compose --env-file .env -f docker/docker-compose.yml exec api python scripts/seed.py`；固定 UUID + `ON CONFLICT DO NOTHING`，可重复执行。业务表缺失时直接报错并提示先迁移。
- **日志**：JSON 行输出到 stdout，交由容器日志驱动或采集器（Loki、ELK）收集。
- **停止**：`docker compose --env-file .env -f docker/docker-compose.yml down` 停止服务并保留数据卷；`down -v` 会删除卷与全部历史，详见 [停止与清理](#停止与清理)。
- **构建 openai-agents 专用镜像**：镜像默认引擎由构建参数固化，构建上下文必须是仓库根。
  ```bash
  docker build -f docker/Dockerfile --build-arg PIPELINE_ENGINE=openai-agents \
    -t sentiment-chat:openai-agents .
  ```
  运行时仍可用环境变量 `PIPELINE_ENGINE` 覆盖。推送到阿里云 ACR（先在控制台创建命名空间与镜像仓库，`docker login` 使用 ACR 固定密码）：
  ```bash
  docker login --username=<阿里云账号> registry.cn-hangzhou.aliyuncs.com
  docker tag sentiment-chat:openai-agents registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-chat:openai-agents
  docker push registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-chat:openai-agents
  ```
  使用 ACR「构建镜像」时，代码源选本仓库，构建上下文设为仓库根，Dockerfile 路径填 `docker/Dockerfile`，构建参数填 `PIPELINE_ENGINE=openai-agents`。
- **多实例**：总连接数 = 实例数 × `pool_size`，需低于 PG `max_connections`；`/health` 作 readiness 探针（DB 异常返回 503），liveness 只探进程存活。

---

## 文档索引

### 项目文档

| 路径 | 内容 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 编码代理协作约定：命令、架构约束、编码与提交规则、测试与文档维护 |
| [`CONTEXT.md`](CONTEXT.md) | 术语表：会话、业务历史、引擎状态、情感、游标、输入文本的规范定义 |
| [`docs/adr/`](docs/adr/) | 架构决策记录；当前 1 条：引擎切换接受上下文重置 |
| [`docs/specs/`](docs/specs/) | 规格说明书；当前 1 份：情感对话服务（用户故事、接缝与测试决策） |
| [`CHANGELOG.md`](CHANGELOG.md) | 文档与设计变更历史 |

### 本仓库课程示例（`docs/`）

按周次整理的框架教学脚本，不是本项目实现代码，但覆盖了本设计依赖的关键 API：

- `01` / `02`：LangGraph Graph API 与 Functional API（图构建 / 备选编排写法）
- `03_基础使用` / `03_持久化`：Agents SDK 入门；LangGraph `InMemorySaver` 多轮记忆
- `04_Session缓存` / `04_回放`：`SQLiteSession` 共享历史；checkpoint 历史回放
- `05_子图调用` / `05_流式输出` / `07_LangGraph-Agent`：子图、流式输出、ReAct 工具循环

> 脚本中曾硬编码 4 个真实 Key，现已改为从 `DASHSCOPE_API_KEY` 读取，**泄露的 Key 必须立即吊销**。另：`03`/`04`/`05` 各有同号文件且缺 `06`，`02_funciton` 存在拼写错误，建议按「主题 + 序号」重命名。

---

变更历史见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

未指定。若需开源，补充 `LICENSE`（建议 MIT 或 Apache-2.0）。
