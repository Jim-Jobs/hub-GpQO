# 情绪支持流水线：LangGraph / OpenAI Agents 双实现

一个可以直接跑起来的最小完整项目：前端输入 → 后端编排 → **进程 1 情感识别** → **进程 2 分支回应**（正向鼓励 / 负面排解 / 中性回应）→ 结构化 JSON 回传前端。

两套框架各实现一遍，业务契约、提示词、前端页面完全一致，只换编排方式，方便横向对比与选型。

```
sentiment-agent-pipeline/
├── openai-agents-version/     # OpenAI Agents SDK 实现（推荐，附 Dockerfile）
└── langgraph-version/         # LangGraph 实现（附 Dockerfile）
```

---

## 1. 整体架构

```
┌──────────────┐   POST /api/chat    ┌──────────────────────────────┐
│  前端进程     │ ──────────────────▶ │  后端进程 (FastAPI)           │
│ index.html   │                     │  校验入参 / 编排 / 统一响应     │
│ 输入 + 展示   │ ◀────────────────── │                              │
└──────────────┘   JSON 结果          │   ┌────────┐   ┌───────────┐ │
                                     │   │ 进程1   │──▶│ 进程2      │ │
                                     │   │情感识别 │   │分支回应    │ │
                                     │   └────────┘   └───────────┘ │
                                     └──────────────────────────────┘
```

**进程 1**：输入用户文本，输出结构化情感结果 `{label, confidence, intensity, emotions, reason}`。
**进程 2**：拿到 label 后路由——`positive → 鼓励`、`negative → 排解`、`neutral → 中性回应`，输出 `{reply, tips}`。
**后端进程**：唯一对外入口，负责参数校验、串联两个阶段、错误包装、耗时与执行轨迹回传。
**前端进程**：单个 HTML 页面（内嵌 CSS/JS），也可拆出去独立部署（已开启 CORS）。

> 说明：这里的"进程"指流水线中的两个**处理阶段**，默认在同一服务内编排，部署只需一个容器。若你确实要拆成两个**物理进程/微服务**，见第 8 节。

---

## 2. 两套实现的差异

| 维度 | openai-agents-version | langgraph-version |
| --- | --- | --- |
| 编排载体 | `Runner.run()` 顺序调用 + 代码路由 | `StateGraph` 节点 + 条件边 |
| 进程 1 | `Agent(output_type=SentimentResult)` 一次调用拿到结构化结果 | `analyze` 节点，`llm.with_structured_output(...)` |
| 进程 2 | 三个独立 Agent：`encourager` / `comforter` / `responder`，按 label 选 Agent | 三个节点：`encourage` / `comfort` / `neutral`，由 `add_conditional_edges` 路由 |
| 状态管理 | 无显式状态，函数内局部变量 | `PipelineState`（TypedDict）在节点间流转 |
| 可观测性 | 每个 Agent 有独立 name，天然适合接 tracing | `graph.get_graph().draw_mermaid()` 可直接导出图结构 |
| 适合场景 | 以"角色分工"为主、要快速接入工具/转交 | 流程复杂、分支多、需要显式状态与断点续跑 |

两者业务代码都是 `app/pipeline.py`（agents 版）与 `app/graph.py`（langgraph 版），可以直接对照阅读。

---

## 3. 本地运行

```bash
cd openai-agents-version          # 或 langgraph-version

python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env              # 填入 OPENAI_API_KEY
set -a && source .env && set +a   # Windows 用 $env:OPENAI_API_KEY="..."

uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000> 即为前端页面；接口文档在 `/docs`。

**没有 API Key 也能跑**：`config.py` 检测到 `OPENAI_API_KEY` 为空时自动降级为内置规则引擎（关键词打分 + 模板回复），`/health` 会返回 `"mock": true`，页面可正常演示。接真实模型只需补 Key。

---

## 4. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 空 | 为空则自动进入 mock 模式 |
| `OPENAI_BASE_URL` | 空 | 留空走 OpenAI 官方；第三方填兼容地址 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_TEMPERATURE` | `0.4` | 采样温度 |
| `LLM_TIMEOUT` | `60` | 单次请求超时（秒） |
| `PIPELINE_MOCK` | `0` | 置 `1` 强制走规则引擎，用于联调 |

常见第三方端点（OpenAI 兼容模式，`base_url` 必填）：

```ini
# 阿里云百炼
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus

# 火山方舟
OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
OPENAI_MODEL=doubao-seed-1-6-250615
```

agents 版已做两处适配第三方端点：`set_default_openai_api("chat_completions")`（非 responses 协议）、`set_tracing_disabled(True)`（不向官方上报 trace，避免非官方 Key 报 401）。

---

## 5. 接口

### `POST /api/chat`

```bash
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"项目又延期了，我真的很累，不想干了"}'
```

```json
{
  "message": "项目又延期了，我真的很累，不想干了",
  "sentiment": {
    "label": "negative",
    "confidence": 0.86,
    "intensity": 4,
    "emotions": ["疲惫", "无力"],
    "reason": "出现“累”“不想干”等消耗性表达，语气消极"
  },
  "reply": "听到这些，你是又累又无力……",
  "tone": "relief",
  "tips": ["做三轮 4-7-8 呼吸", "写三行情绪日记"],
  "elapsed_ms": 2380,
  "trace": [
    {"stage": "stage1", "name": "情感识别节点", "elapsed_ms": 1120, "detail": "negative / 置信度 0.86 / 强度 4"},
    {"stage": "stage2", "name": "排解分支（relief）", "elapsed_ms": 1260, "detail": "输出 182 字，3 条建议"}
  ]
}
```

其它端点：

- `GET /health`：健康检查，返回当前框架、模型、是否 mock（Docker HEALTHCHECK 也用它）
- `GET /docs`：Swagger
- `GET /api/graph`（仅 langgraph 版）：返回 Mermaid 图结构

---

## 6. Docker 打包（基于 openai-agents 版）

### 6.1 Dockerfile 做了什么

- `python:3.12-slim` 基础镜像，`ARG BASE_IMAGE` 可换成阿里云 ACR 的加速地址
- **依赖层与代码层分离**：只改业务代码不会重装依赖
- `PIP_INDEX_URL` 默认指向阿里云 PyPI 镜像，国内构建不用等
- 非 root 用户（UID 10001）运行
- 内置 `HEALTHCHECK`（用 Python 标准库探测，不额外装 curl，镜像更小）
- `exec uvicorn` 作为 PID 1，容器能正常收到 `SIGTERM` 优雅退出

### 6.2 构建与运行

```bash
cd openai-agents-version

# 构建
docker build -t sentiment-agents:1.0.0 .

# Docker Hub 不可达时（国内网络常见），换基础镜像源再构建：
# docker build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
#   -t sentiment-agents:1.0.0 .

# 运行（推荐用 env-file，别把 Key 写进镜像）
docker run -d --name sentiment-agents \
  -p 8000:8000 \
  --env-file .env \
  --restart unless-stopped \
  sentiment-agents:1.0.0

# 验证
curl http://127.0.0.1:8000/health
docker logs -f sentiment-agents
```

Windows 上也可直接运行脚本（会自动寻找 `docker.exe`，Docker Desktop 未进 PATH 时同样可用）：

```powershell
cd openai-agents-version
.\scripts\build.ps1
# 构建 + 打标签 + 推送阿里云
.\scripts\build.ps1 -Registry registry.cn-hangzhou.aliyuncs.com -Namespace your-ns -Push
```

### 6.3 推送阿里云镜像服务（ACR）

1. 打开 <https://cr.console.aliyun.com>，创建**个人版**或**企业版**实例，并创建命名空间（如 `your-ns`）、镜像仓库（如 `sentiment-agents`）。
2. 本地登录（账号为阿里云账号全名，密码在控制台"访问凭证"里设置）：

```bash
docker login --username=<你的阿里云账号> registry.cn-hangzhou.aliyuncs.com
```

3. 打标签并推送（个人版地址形如 `registry.cn-hangzhou.aliyuncs.com/<命名空间>/<仓库名>:<版本>`）：

```bash
docker tag sentiment-agents:1.0.0 registry.cn-hangzhou.aliyuncs.com/your-ns/sentiment-agents:1.0.0
docker push registry.cn-hangzhou.aliyuncs.com/your-ns/sentiment-agents:1.0.0
```

Linux/mac 上可用脚本一次完成：

```bash
REGISTRY=registry.cn-hangzhou.aliyuncs.com NAMESPACE=your-ns VERSION=1.0.0 \
  ./scripts/build-push.sh push
```

4. 服务器上拉取运行：

```bash
docker login --username=<账号> registry.cn-hangzhou.aliyuncs.com
docker pull registry.cn-hangzhou.aliyuncs.com/your-ns/sentiment-agents:1.0.0
docker run -d --name sentiment-agents -p 8000:8000 --env-file .env \
  registry.cn-hangzhou.aliyuncs.com/your-ns/sentiment-agents:1.0.0
```

也可以完全在控制台完成打包：ACR 企业版支持「代码源 + 构建规则」自动构建镜像，把本仓库推到 GitHub/云效后配置 Dockerfile 路径（`openai-agents-version/Dockerfile`）与构建上下文即可，无需本地 Docker。

### 6.4 docker compose

```bash
cd openai-agents-version
cp .env.example .env   # 填 Key
docker compose up -d --build
```

---

## 7. 排查清单

| 现象 | 原因与处理 |
| --- | --- |
| `/health` 返回 `mock: true` | 没读到 `OPENAI_API_KEY`，检查 `--env-file` / compose 的 `env_file` |
| 502 + `401 Unauthorized` | Key 与 `OPENAI_BASE_URL` 不匹配，或第三方端点不支持 responses 协议 |
| 502 + `model not found` | `OPENAI_MODEL` 写成了展示名，需填真实模型 ID（如 `qwen-plus`） |
| 构建卡在 pip | 确认 `PIP_INDEX_URL` 生效：`docker build --build-arg PIP_INDEX_URL=...` |
| 构建报 `docker.io/docker/dockerfile:1` 无法解析 / `python:3.12-slim` 拉不下来 | 国内网络到 Docker Hub 不通。Dockerfile 已去掉 `syntax` 头，基础镜像用 `--build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim`（或换成你 ACR 里的 python 镜像） |
| 镜像较大 | 已做分层与 `--no-cache-dir`；进一步可换 `python:3.12-alpine`（需自行编译依赖，不推荐） |
| 中文日志乱码 | 已设 `PYTHONUNBUFFERED=1` 与 `TZ=Asia/Shanghai`，宿主机终端需支持 UTF-8 |

---

## 8. 如果要真正的"两个进程"

当前默认单容器内编排，延迟最低、运维最简单。需要物理隔离时有两种演进路径：

1. **两容器 + HTTP**：把进程 1 单独拆成 `sentiment-service`（`POST /analyze`），后端按顺序调用两个服务；再补超时、重试、熔断。
2. **两容器 + 消息队列**：进程 1 消费 `input` 队列 → 产出 `sentiment` 主题 → 进程 2 消费并按 label 分到不同消费者组 → 后端用 SSE/WebSocket 把结果推给前端。适合高并发与削峰。

改造点集中在一处：把 `run_pipeline()` 里的两次调用替换成两次远程调用（HTTP 或 MQ），`ChatResponse` 契约不用动，前端无需修改。
