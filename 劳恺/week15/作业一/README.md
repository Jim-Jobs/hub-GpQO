# 情感识别与情绪响应多 Agent 系统

基于 **LangGraph** 与 **OpenAI Agents SDK** 的两套等价实现，Vue 3 + FastAPI 前后端分离架构。

完整需求与设计说明见 [`README_本地运行版.md`](README_本地运行版.md)。

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

复制 `.env.example` 为 `.env`，填写 `OPENAI_API_KEY` 与 `OPENAI_MODEL`。
其余项（端口、数据库路径、前端地址）保持默认即可。

也可以不建 `.env`，把 key 直接放进操作系统环境变量（代码优先读 `os.environ`）：

```powershell
$env:OPENAI_API_KEY = "sk-xxx"    # Windows PowerShell
$env:OPENAI_MODEL  = "your_model_id"
```

```bash
export OPENAI_API_KEY="sk-xxx"   # macOS / Linux
export OPENAI_MODEL="your_model_id"
```

### 3. 启动后端

需要 6 个进程。使用多进程启动脚本：

```bash
python scripts/start_dev.py                      # 启动全部 6 个服务
python scripts/start_dev.py --framework langgraph   # 只启动 LangGraph 这 3 个
```

也可以分别启动：

```bash
python scripts/run_service.py langgraph agent1       # :8001
python scripts/run_service.py langgraph agent2       # :8002
python scripts/run_service.py langgraph orchestrator # :8000
python scripts/run_service.py openai_agents agent1       # :9001
python scripts/run_service.py openai_agents agent2       # :9002
python scripts/run_service.py openai_agents orchestrator # :9000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 ，可在页面右上角切换两套框架。

## 服务地址

| 服务 | 地址 |
|---|---|
| Vue 前端 | http://localhost:5173 |
| LangGraph API | http://localhost:8000 （文档 /docs） |
| LangGraph Agent 1 / 2 | http://localhost:8001 / :8002 |
| Agents SDK API | http://localhost:9000 （文档 /docs） |
| Agents SDK Agent 1 / 2 | http://localhost:9001 / :9002 |

## 常用命令

```bash
# 全部测试
python -m pytest -q

# 单个测试文件 / 单个用例
python -m pytest tests/test_quality.py -q
python -m pytest tests/test_orchestrator.py::TestChatStream::test_happy_path_event_sequence -q

# 前端构建
cd frontend && npm run build

# 端到端联调（用模拟模型，不需要真实 API Key）
python scripts/e2e_check.py
```

## Docker 部署（只打包 OpenAI Agents SDK 版）

### 打包了哪些文件

镜像里只有跑 Agents SDK 版必需的东西：

| 进镜像 | 说明 |
|---|---|
| `backends/__init__.py` | 包入口 |
| `backends/shared/` | 框架无关层：契约、数据库、质量检查、编排器 |
| `backends/openai_agents/` | 本版本实现：`workflow_oa.py` + `agent1/` + `agent2/` + `orchestrator/` |
| `docker/requirements-agents.txt` | 依赖清单（已去掉 langgraph / langchain-openai） |

**不进镜像**：`backends/langgraph/`（另一套实现）、`frontend/`、`tests/`、`scripts/`、`experiments/`、`data/`（改用 volume）、`.env`（绝不进镜像）、`pyproject.toml`（运行时用 `PYTHONPATH` 即可，不需要打包安装）。

### 构建

```bash
# 构建上下文是项目根目录
docker build -f docker/Dockerfile -t emotion-agents:latest .
```

镜像约 370MB（`python:3.12-slim` + 依赖），产出 `emotion-agents:latest`。
镜像存储在 Docker Desktop 的 WSL2 虚拟磁盘里：
`C:\Users\<用户名>\AppData\Local\Docker\wsl\disk\docker_data.vhdx`，用 `docker images` 查看。

### 运行（三个服务 + Compose）

```bash
# 在项目根目录执行；API Key 用宿主机环境变量提供，不落盘
export OPENAI_API_KEY="sk-xxx"          # Windows PowerShell: $env:OPENAI_API_KEY = "sk-xxx"
export OPENAI_MODEL="your_model_id"

docker compose up --build -d             # 起 编排器:9000 + Agent1:9001 + Agent2:9002
docker compose logs -f
docker compose down                      # 停止（加 -v 连数据卷一起删）
```

变量优先级是 **宿主机环境变量 > 项目根目录 `.env`**。

容器之间的 Agent 地址由 compose 覆盖成服务名（`http://agent1:9001`），不能沿用 `.env` 里的 `127.0.0.1`。
聊天历史落在名为 `oa-data` 的 volume 里，容器重建后数据仍在。

### API Key 的三种给法

| 方式 | 怎么写 | key 落在哪 |
|---|---|---|
| **① 宿主机环境变量**（推荐） | `$env:OPENAI_API_KEY = "sk-xxx"` 后 `docker compose up -d` | 只在内存/环境变量里，镜像和文件都没有 |
| **② 烘焙进镜像**（本地演示用） | 见下方命令 | 镜像的文件层里；`docker history` **看不到**，但 `docker save` 后能翻出来 |
| **③ 运行时挂载 secret 文件** | 见 `docker-compose.yml` 末尾的注释 | 完全不进镜像，也不在环境变量里 |

方式 ② 的构建命令：

```bash
echo -n "sk-你的key" > api_key.txt
docker build -f docker/Dockerfile -t emotion-agents:latest \
  --secret id=api_key,src=./api_key.txt .
```

镜像里会多出 `/app/secrets/api_key`（权限 600），应用通过 `OPENAI_API_KEY_FILE` 读取它。
`docker history` 里不会出现这个 key（BuildKit 的 secret 不写进构建历史）。

> ⚠️ 方式 ② 的 key 仍然在镜像的文件层里，**不要把这种镜像推到公开仓库**。
> 生产环境请用方式 ① 或 ③。

### 运行（单容器）

```bash
docker run --rm -p 9000:9000 \
  --env OPENAI_API_KEY --env OPENAI_MODEL \
  emotion-agents:latest
```

`--env VAR`（不带 `=`）表示从宿主机环境变量透传，key 不出现在任何文件里。
默认启动编排器，要跑 Agent 请覆盖 `command`。

### 只导出镜像

```bash
docker build -f docker/Dockerfile -t emotion-agents:latest .
docker save -o emotion-agents.tar emotion-agents:latest    # 导出到当前目录
docker load -i emotion-agents.tar                          # 在别的机器导入
```

## 目录结构

```text
├── backends/                # 全部后端代码
│   ├── shared/              # 两套框架共用的框架无关代码
│   │   ├── config/          # 配置与框架画像（端口、Agent 地址、数据库路径）
│   │   ├── constants/       # 情绪分类、响应策略、兜底内容
│   │   ├── schemas/         # Pydantic 数据契约
│   │   ├── llm/             # 提示词、输出解析、模型配置
│   │   ├── db/              # SQLAlchemy 模型与数据访问
│   │   ├── policy/          # 策略选择规则
│   │   ├── quality/         # 回复质量检查
│   │   ├── clients/         # Agent HTTP 客户端
│   │   ├── errors.py        # 跨框架共享异常
│   │   └── orchestrator/    # FastAPI 编排器（聊天 SSE + 会话 API）
│   ├── langgraph/
│   │   ├── workflow_lg.py   # ★ LangGraph 工作流（模型调用 + Agent1 图 + Agent2 图）
│   │   ├── agent1/main.py   # FastAPI 服务（只做 HTTP 协议）
│   │   ├── agent2/main.py
│   │   └── orchestrator/main.py
│   └── openai_agents/
│       ├── workflow_oa.py   # ★ Agents SDK 工作流（模型调用 + Agent1 + Agent2 路由/移交）
│       ├── agent1/main.py   # FastAPI 服务（只做 HTTP 协议）
│       ├── agent2/main.py
│       └── orchestrator/main.py
├── frontend/               # Vue 3 + Vite 前端
├── scripts/                # 启动脚本、模拟模型、端到端检查
├── tests/                  # pytest 测试
├── data/                   # 两套框架各自的 SQLite 数据库
└── experiments/            # 实验脚本与结果（后续阶段）
```

### 对比两套框架实现

两个框架的差异集中在 `workflow_lg.py` 与 `workflow_oa.py` 两个文件里，
章节一一对应，并暴露同名同签名的 `run_analyze()` / `run_respond()`：

```bash
diff backends/langgraph/workflow_lg.py backends/openai_agents/workflow_oa.py
```

| 对比点 | LangGraph | OpenAI Agents SDK |
|---|---|---|
| 模型调用 | `ChatOpenAI.ainvoke()` | `Runner.run()` |
| 状态载体 | 显式 `TypedDict` 状态 | SDK 内部托管 |
| Agent 1 重试 | 图内 `repair` 节点 + 条件边自环 | 入口函数里循环重跑 |
| Agent 2 策略路由 | **条件边**分流到 3 个生成分支 | **handoff** 移交给 3 个专员 Agent |

## 核心流程

```text
用户消息 → FastAPI 编排器 → 加载完整历史 → Agent 1 情绪识别
        → Agent 2 情绪响应 → 质量检查 →(失败则重试一次)→ 保存会话 → SSE 返回
```

Agent 1 与 Agent 2 之间不直接通信，一律由编排器调度；两套框架各自使用独立数据库。
