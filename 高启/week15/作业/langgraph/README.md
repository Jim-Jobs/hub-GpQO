# LangGraph 情感识别与回应

## 目标与流程

聊天页面将文字发给 FastAPI 后端。后端返回请求编号后，将任务写入 Redis Stream `chat:requests`。独立的情感识别进程用 LangGraph 节点和 LangChain 调用 DeepSeek，将识别结果写入 `chat:sentiments`；独立的回复进程生成鼓励、排解或中性回应。API 通过 SSE 发送“排队中、识别情感中、生成回复中、完成/失败”事件。

处理过程拆成五步：

1. API 接收文字，生成请求编号、写入请求流，并返回 SSE 订阅地址。
2. API 发布“排队中”事件；页面连接 SSE 并显示收到的阶段状态。
3. 情感识别进程运行 LangGraph 图中的识别 Agent 节点，发布识别状态并写入情感结果流。
4. 回复进程运行 LangGraph 图中的回复 Agent 节点，发布生成状态并产生最终结果事件。
5. API 将该请求的 Redis 事件流转成 SSE，页面按阶段更新消息气泡并显示最终结果。

LangGraph 项目不依赖框架提供的 `Agent` 类。项目单独创建 `backend/agent.py`，放 DeepSeek 模型适配和情感识别/回复两个 Agent 节点；`backend/workflow.py` 定义 LangGraph 状态与图连接；提示词模板独立放在 `backend/prompts.py`。两个工作进程分别执行自己的图，跨进程数据仍使用共同 Redis 消息契约。Python 函数文档字符串按用途、Args、Returns、Raises 说明数据契约。

## 配置和启动

复制 `.env.example` 为 `.env`，填写以下三个 DeepSeek 配置：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
REDIS_URL=redis://localhost:6381/0
REDIS_HOST_PORT=6381
API_HOST=localhost
API_PORT=8000
FRONTEND_PORT=8080
```

在此目录运行：

```powershell
Copy-Item .env.example .env
# 编辑 .env 后启动
docker compose up --build
```

打开 <http://localhost:8080>。API 文档在 <http://localhost:8000/docs>。停止服务：`docker compose down`。Redis 数据使用 Docker volume 保存。

### 本机运行应用，只用 Docker 启动 Redis

这种方式不会构建 API/前端镜像，因此只需要 Docker Redis 镜像，以及本机 Python 和 Node.js 环境。先在本机终端进入本项目目录，并确保 `.env` 包含 DeepSeek、Redis、API 和前端地址/端口配置。API 和 worker 会自动读取 `.env`；Docker Compose 会把容器内 Redis 地址覆盖为 `redis://redis:6379/0`。

```bash
docker compose up -d redis
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

保持 Redis 在后台运行。分别在三个终端启动 API、识别 worker 和回复 worker；每个终端都先进入项目目录并激活 `.venv`：

```bash
python -m backend.run_api
```

```bash
python -m backend.workers sentiment
```

```bash
python -m backend.workers reply
```

再开一个终端提供前端静态页面：

```bash
node --env-file=.env frontend/server.mjs
```

浏览器访问 `http://API_HOST:FRONTEND_PORT`。前端 Node.js 服务要求 Node.js 20.6 或更高版本。关闭 Redis：`docker compose down`。本机应用通过 `REDIS_URL` 中的主机端口访问 Redis；Compose 内部服务仍通过 `redis:6379` 通信。Windows Conda 用户可在 PowerShell 激活 `py312` 后运行三个 Python 进程；前端命令为 `node --env-file=.env frontend/server.mjs`。

## 测试和评测

在 `project--04` 根目录运行共享测试和数据校验：

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```

在本项目目录运行本地单元测试：

```powershell
python -m unittest discover -s tests -v
```

启动本项目的 Redis、API、两个 worker 后，在 `project--04` 根目录批量运行 30 条样例：

```powershell
python -m test.run_cases --api-url http://localhost:8000 --framework langgraph --results .\test\results\langgraph\run.jsonl
```

这会触发 60 次模型请求并消耗 DeepSeek API 额度。随后汇总 JSONL：

```powershell
python -m test.evaluate --results .\test\results\langgraph\run.jsonl
```

评测使用共享的 30 条标注样例；输出 Accuracy、Macro-F1、每类 precision/recall/F1、格式有效率、延迟、人工评分均值和失败数。API 不返回 Token 用量，故该指标暂不可用。通过阈值尚未设定。

worker 收到 `Ctrl+C` 会关闭 Redis 连接并正常退出；重启 worker 后才能使用代码更新。

## 工程结构

- `backend/api.py`：接收页面请求、投递消息并等待对应结果。
- `backend/workers.py`：运行情感识别或回复消费者；通过命令参数区分进程。
- `backend/agent.py`：两个 Agent 节点和 DeepSeek 模型适配。
- `backend/workflow.py`：两张 LangGraph 图的状态、节点和边。
- `backend/prompts.py`：识别与回应步骤使用的提示词模板。
- `backend/schemas.py`：API、Redis 和 SSE 业务契约。
- `frontend/index.html`、`frontend/server.mjs`：聊天界面和 Node.js 静态文件/API 配置服务。
- `docker-compose.yml`：启动 API、两个工作进程、Node.js 前端和 Redis。

## 数据结构

两个实现的**业务数据契约保持一致**，方便使用同一组测试样例比较效果。框架内部状态则按各自框架定义，不直接暴露给 API 或 Redis 消息。

### 共同业务契约

- API 请求 `ChatRequest`：`text`。
- API 接受响应 `ChatAccepted`：`request_id`、`events_url`。请求提交后立即返回，前端通过 SSE 订阅进度。
- Redis 请求消息 `RedisChatJob`：`request_id`、`text`。
- Redis 情感结果 `RedisSentimentResult`：`request_id`、`text`、`sentiment`、`reason`；由情感识别进程交给回复进程。
- 最终结果 `ChatResult`：`request_id`、`sentiment`、`reason`、`reply`。
- SSE `WorkflowEvent`：共同包含 `request_id`，并用 `event_type` 区分进度、完成和失败事件。进度阶段为 `queued`、`sentiment_processing`、`reply_processing`；完成事件携带 `ChatResult`；失败事件携带失败阶段、错误码和面向用户的消息。
- 情感类别统一为 `positive`、`negative`、`neutral`。

### LangGraph 内部状态

LangGraph 使用图状态在节点之间传递本进程的数据，状态只服务于 LangGraph，不作为 API 或 Redis 的对外契约。情感识别图状态包含 `request_id`、`text`、`sentiment`、`reason`；回复图使用原文和识别结果生成 `reply`。由于识别和回复运行在独立进程中，二者之间仍通过共同的 `RedisSentimentResult` 消息传递。

图状态使用 `TypedDict` 描述；API、Redis 和 Agent 结构化输出由 Pydantic 校验。每条用户输入单独运行，不启用 LangGraph checkpoint 或多轮记忆。

评测样例与运行消息分开存放。30 条测试样例共用于两套实现；情感标注字段固定，回复评分维度和分值范围仍待确认。

共同消息类型和事件结构见下方“数据结构”。AgentLoop 原文采集、脱敏、保留期及访问范围尚待确认，本项目暂不配置云端内容采集。请勿提交包含真实密钥的 `.env`。
