# 第 15 周作业：情感识别与回应（LangGraph / openai-agents）

## 作业需求实现对照

| 要求 | LangGraph 版 | openai-agents 版 |
| --- | --- | --- |
| 进程 1：接受请求进行情感识别 | `langgraph_version/graph.py` 的 `recognize_sentiment` 节点，结构化输出 `SentimentResult` | `openai_agents_version/pipeline.py` 的 `sentiment_agent`，`output_type=SentimentResult` |
| 进程 2：正向鼓励、负面排解 | `route_response` 条件边 + `encourage` / `comfort` / `neutral` 节点 | 根据识别结果选择 `encourage_agent` / `comfort_agent` / `neutral_agent` |
| 后端进程：串联前端与进程 1、2 | `langgraph_version/backend.py`（FastAPI，端口 8001） | `openai_agents_version/backend.py`（FastAPI，端口 8002） |
| 前端进程：用户输入 | `langgraph_version/frontend.py`（Streamlit）；后端 `/` 也自带网页 | `openai_agents_version/frontend.py`（Streamlit）；后端 `/` 也自带网页 |

流程：`用户输入 → 后端 /api/chat → 进程 1 情感识别 → 进程 2 分支回应 → 返回前端`

- 正向（positive）→ 鼓励
- 负向（negative）→ 排解
- 中性（neutral）→ 平和回应

## 目录结构

```
week15-assignment/
├── common/                     # 两个版本复用的配置、数据结构、mock 逻辑、网页模板
├── langgraph_version/          # 作业 1：LangGraph 实现
│   ├── graph.py                # 进程 1 + 进程 2 工作流
│   ├── backend.py              # 后端进程
│   └── frontend.py             # 前端进程
├── openai_agents_version/      # 作业 1：openai-agents 实现（作业 2 打包对象）
│   ├── pipeline.py             # 进程 1 + 进程 2 Agent 串联
│   ├── backend.py              # 后端进程
│   └── frontend.py             # 前端进程
├── Dockerfile                  # 作业 2：openai-agents 版镜像
├── docker-compose.yml
├── requirements.txt            # 本地运行两个版本
└── requirements-docker.txt     # 镜像内精简依赖
```

## 一、环境准备

```powershell
cd "d:/AI学习/第15周：Reasoning、Sanbox和ClaudeCode Codex应用/week15-assignment"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
OPENAI_API_KEY=sk-你的密钥
OPENAI_BASE_URL=          # 兼容 OpenAI 协议的中转地址可填，否则留空
OPENAI_MODEL=gpt-4o-mini
USE_MOCK_LLM=0
```

> **未配置 `OPENAI_API_KEY` 时会自动启用 mock 模式**：使用内置的关键词规则完成情感识别与回复生成，
> 界面和 API 完全可用，方便在没有密钥的机器上演示流程。设置密钥后即切换为真实模型。
> 也可显式设置 `USE_MOCK_LLM=1` 强制使用 mock 模式。

## 二、运行作业 1

需要开 3 个终端（先激活虚拟环境）。

### 方案 A：LangGraph 版

终端 1（后端进程）：

```powershell
python -m uvicorn langgraph_version.backend:app --host 0.0.0.0 --port 8001 --reload
```

终端 2（前端进程）：

```powershell
streamlit run langgraph_version/frontend.py --server.port 8501
```

终端 3（测试请求，可选）：

```powershell
Invoke-RestMethod -Uri http://localhost:8001/api/chat -Method Post `
  -ContentType "application/json" -Body '{"message":"今天项目上线了，我太开心了！"}'
```

打开 `http://localhost:8501` 使用 Streamlit 前端；也可直接打开 `http://localhost:8001` 使用后端自带的网页。

### 方案 B：openai-agents 版

端口改为 8002：

```powershell
# 终端 1：后端
python -m uvicorn openai_agents_version.backend:app --host 0.0.0.0 --port 8002 --reload

# 终端 2：前端
streamlit run openai_agents_version/frontend.py --server.port 8502

# 终端 3：测试
Invoke-RestMethod -Uri http://localhost:8002/api/chat -Method Post `
  -ContentType "application/json" -Body '{"message":"最近压力很大，感觉很累"}'
```

打开 `http://localhost:8502` 或 `http://localhost:8002`。

返回示例：

```json
{
  "sentiment": "negative",
  "strategy": "排解",
  "reply": "听起来你现在承受了不少压力……",
  "sentiment_reason": "命中 2 个负向关键词……",
  "confidence": 0.85
}
```

## 三、作业 2：Docker 镜像打包

Dockerfile 基于作业 1 的 **openai-agents 版本**，构建上下文为项目根目录。

### 1. 本地构建与运行

```powershell
cd "d:/AI学习/第15周：Reasoning、Sanbox和ClaudeCode Codex应用/week15-assignment"

# 构建镜像
docker build -t week15-emotion-agent:latest .

# 运行（无密钥时自动 mock 模式）
docker run --rm -p 8002:8002 week15-emotion-agent:latest

# 运行（使用真实模型，密钥由环境变量注入，不写进镜像）
docker run --rm -p 8002:8002 `
  -e OPENAI_API_KEY="sk-你的密钥" `
  -e OPENAI_BASE_URL="你的中转地址" `
  -e OPENAI_MODEL="gpt-4o-mini" `
  week15-emotion-agent:latest
```

浏览器访问 `http://localhost:8002` 即可输入文字，也可调用 `POST /api/chat`。

### 2. 使用 docker compose

```powershell
docker compose up --build -d
docker compose logs -f
docker compose down
```

compose 会从 `.env` 读取密钥并注入容器。

### 3. 推送到阿里云镜像服务（ACR）

先在阿里云控制台创建「个人版/企业版实例 → 命名空间 → 镜像仓库」，然后：

```powershell
# 1) 登录（站在地域换成分区所在地域）
docker login --username=你的阿里云账号 registry.cn-hangzhou.aliyuncs.com

# 2) 打标签
docker tag week15-emotion-agent:latest `
  registry.cn-hangzhou.aliyuncs.com/你的命名空间/week15-emotion-agent:v1

# 3) 推送
docker push registry.cn-hangzhou.aliyuncs.com/你的命名空间/week15-emotion-agent:v1

# 4) 验证拉取
docker pull registry.cn-hangzhou.aliyuncs.com/你的命名空间/week15-emotion-agent:v1
```

> 注意：`.dockerignore` 已排除 `.env`，密钥不会进入镜像；请通过 `-e` 或容器环境变量注入。

## 四、关键设计说明

1. **进程解耦**：两个框架版本都把「情感识别」和「回应生成」拆成独立步骤，后端仅负责编排，前端只与 `/api/chat` 交互。
2. **结构化输出**：进程 1 使用 Pydantic 模型 `SentimentResult` 约束输出，避免解析自由文本导致路由失败。
3. **条件路由**：LangGraph 使用 `add_conditional_edges`；openai-agents 使用 Python 分支选择 Agent。
4. **可运行性**：无密钥时自动 mock，保证代码在离线教学环境也能完整演示四个进程。
