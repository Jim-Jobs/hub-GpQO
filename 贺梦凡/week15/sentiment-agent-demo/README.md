# 情感识别与回应 Agent Demo

分别使用 **LangGraph** 与 **OpenAI Agents SDK** 实现同一业务逻辑：前端输入 → 后端编排 → 情感识别 → 正向鼓励 / 负向排解。

---

## 1. 业务目标

| 进程 | 职责 |
|------|------|
| **进程 1（情感识别）** | 接收用户文本，输出情感标签（`positive` / `negative` / `neutral`）及简要依据 |
| **进程 2（回应生成）** | 根据识别结果：正向 → 鼓励；负向 → 排解安抚；中性 → 温和陪伴式回应 |
| **后端进程** | 提供 HTTP API，串联进程 1 → 进程 2，统一对外暴露 |
| **前端进程** | 简单 Web 页面，接收用户输入并展示识别结果与回应 |

---

## 2. 总体架构

```
┌─────────────┐     HTTP POST /chat      ┌──────────────────────────────────┐
│  前端进程    │ ───────────────────────► │           后端进程                │
│  (静态页 /   │ ◄─────────────────────── │  FastAPI                          │
│   简单 HTML) │     JSON 响应            │    ├─ 编排层（串联 1→2）            │
└─────────────┘                          │    ├─ 进程1：情感识别 Agent/Node   │
                                         │    └─ 进程2：回应生成 Agent/Node   │
                                         └──────────────────────────────────┘
                                                      │
                                                      ▼
                                              LLM（OpenAI 兼容接口）
                                              如 DeepSeek / OpenAI
```

### 数据流

1. 用户在前端输入一句话  
2. 前端 `POST /api/chat`，body：`{ "text": "..." }`  
3. 后端调用「进程 1」得到情感结果  
4. 后端将原文 + 情感结果交给「进程 2」生成回应  
5. 返回：`{ "sentiment": "...", "reason": "...", "reply": "..." }`  
6. 前端展示识别结果与最终回应  

---

## 3. 目录结构

```
sentiment-agent-demo/
├── README.md
├── .env.example / .env
├── requirements.txt
│
├── shared/                      # 共用契约
│   ├── config.py                # 环境变量、路径
│   ├── schemas.py               # ChatRequest / ChatResponse / SentimentResult
│   └── prompts.py               # 进程 1 / 进程 2 提示词
│
├── langgraph_impl/              # 方案 A：LangGraph
│   ├── llm.py                   # ChatOpenAI（兼容接口）
│   ├── nodes.py                 # sentiment_node / respond_node
│   ├── graph.py                 # StateGraph 串联 + run_pipeline
│   └── server.py                # FastAPI 后端（默认 :8001）
│
├── openai_agents_impl/          # 方案 B：OpenAI Agents
│   ├── client_setup.py          # AsyncOpenAI 默认客户端
│   ├── agents.py                # SentimentAgent / RespondAgent
│   ├── pipeline.py              # Runner 顺序调用串联
│   └── server.py                # FastAPI 后端（默认 :8002）
│
└── frontend/
    └── index.html               # 前端（两套后端均可托管）
```

> 两套实现共享 API 契约与前端；仅编排层不同。

---

## 4. 共享契约

### 4.1 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 主接口：情感识别 + 回应 |
| `GET`  | `/health` | 健康检查 |
| `GET`  | `/` | 托管前端页面 |

**请求**

```json
{
  "text": "今天面试通过了，好开心！"
}
```

**响应**

```json
{
  "text": "今天面试通过了，好开心！",
  "sentiment": "positive",
  "reason": "表达了成功与喜悦",
  "reply": "太棒了！这次通过说明你的准备很扎实，继续保持这份节奏！"
}
```

### 4.2 情感枚举

- `positive`：正向 → 进程 2 走「鼓励」策略  
- `negative`：负向 → 进程 2 走「排解」策略  
- `neutral`：中性 → 进程 2 走「温和陪伴」策略  

### 4.3 环境变量（`.env`）

```env
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
# 可选：分别指定端口
LANGGRAPH_PORT=8001
AGENTS_PORT=8002
```

---

## 5. 方案 A：LangGraph 实现

### 5.1 设计思路

用 **StateGraph** 把「进程 1 / 进程 2」建模为图中的两个节点，边固定为顺序执行：

```
START → sentiment_node → respond_node → END
```

### 5.2 状态（State）

```python
class GraphState(TypedDict):
    text: str                 # 用户原文
    sentiment: str            # positive | negative | neutral
    reason: str               # 识别依据
    reply: str                # 最终回应
```

### 5.3 节点职责

| 节点 | 对应进程 | 行为 |
|------|----------|------|
| `sentiment_node` | 进程 1 | 调用 LLM，结构化输出 `sentiment` + `reason`，写回 State |
| `respond_node` | 进程 2 | 读取 `text` + `sentiment`，按策略生成 `reply` |

进程 2 可用 **条件边**（可选增强）：根据 `sentiment` 路由到 `encourage_node` / `comfort_node` / `neutral_node`；MVP 用单一 `respond_node` + 提示词内分支即可。

### 5.4 后端串联

`server.py`：

1. 接收 `POST /api/chat`  
2. `graph.invoke({"text": req.text})`  
3. 将 State 映射为 API 响应  

### 5.5 关键依赖

- `langgraph`
- `langchain-openai`（或兼容 OpenAI 接口的 chat model）
- `fastapi` / `uvicorn`

---

## 6. 方案 B：OpenAI Agents SDK 实现

### 6.1 设计思路

用两个独立 **Agent** 表示进程 1 / 进程 2，后端用 **Runner** 顺序调用完成串联（等价于 LangGraph 的线性图）。

```
用户输入
   │
   ▼
SentimentAgent  ──输出──►  {sentiment, reason}
   │
   ▼
RespondAgent    ──输入──►  原文 + 情感结果 ──► reply
```

### 6.2 Agent 定义

| Agent | 对应进程 | Instructions 要点 |
|-------|----------|-------------------|
| `SentimentAgent` | 进程 1 | 只做情感分类，输出结构化 JSON |
| `RespondAgent` | 进程 2 | 根据情感标签选择鼓励 / 排解 / 陪伴话术 |

推荐为进程 1 配置 **structured output**（Pydantic 模型），避免解析不稳定。

### 6.3 编排（后端进程）

```text
result1 = Runner.run(SentimentAgent, user_text)
result2 = Runner.run(RespondAgent, f"原文:{text}\n情感:{sentiment}\n依据:{reason}")
return ChatResponse(...)
```

可选进阶：用 **Agent as Tool** 或 **handoffs**，让一个 Coordinator Agent 调用/移交到上述两个 Agent；MVP 用显式顺序调用更清晰，便于对照 LangGraph。

### 6.4 关键依赖

- `openai-agents`（Agents SDK）
- `openai`
- `fastapi` / `uvicorn`
- `python-dotenv`

---

## 7. 前端进程

单页 `frontend/index.html`：

- 文本输入框 +「发送」按钮  
- 展示区：情感标签（可着色）、识别依据、Agent 回应  
- 通过 `fetch('/api/chat')` 调用当前所连后端  
- 无构建工具，直接由 FastAPI `StaticFiles` / `FileResponse` 托管  

---

## 8. 两套实现对照

| 维度 | LangGraph | OpenAI Agents |
|------|-----------|---------------|
| 进程抽象 | Graph Node | Agent |
| 串联方式 | 边（edge）固定顺序 | Runner 顺序调用（或 handoff） |
| 状态传递 | 共享 `State` | 人工拼接上下文 / 结构化输出 |
| 分支扩展 | 条件边天然合适 | 指令分支或 handoff 路由 |
| 学习侧重点 | 图编排、状态机 | Agent 角色、Runner、工具/移交 |

业务结果应对齐：**同一输入，两套后端返回相同 schema**，便于 A/B 对比。

---

## 9. 运行方式

在项目根目录 `sentiment-agent-demo/` 下执行：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
# 若无 .env：copy .env.example .env 并填入 API Key

# 方案 A — LangGraph
python -m langgraph_impl.server
# 或：uvicorn langgraph_impl.server:app --reload --port 8001 --app-dir .

# 方案 B — OpenAI Agents（另开终端）
python -m openai_agents_impl.server
# 或：uvicorn openai_agents_impl.server:app --reload --port 8002 --app-dir .
```

浏览器访问：

- LangGraph：http://127.0.0.1:8001  
- OpenAI Agents：http://127.0.0.1:8002  

快速自测：

```bash
curl -X POST http://127.0.0.1:8001/api/chat -H "Content-Type: application/json" -d "{\"text\":\"今天面试通过了，好开心！\"}"
curl -X POST http://127.0.0.1:8002/api/chat -H "Content-Type: application/json" -d "{\"text\":\"连续加班好累，感觉撑不住了\"}"
```

---

## 10. 代码已落地说明

当前仓库已按上表完成两套实现，逻辑对应关系：

| 业务进程 | LangGraph | OpenAI Agents |
|----------|-----------|---------------|
| 进程 1 情感识别 | `nodes.sentiment_node` | `SentimentAgent` |
| 进程 2 鼓励/排解 | `nodes.respond_node` | `RespondAgent` |
| 后端串联 | `graph.run_pipeline` | `pipeline.run_pipeline` |
| HTTP + 前端托管 | `langgraph_impl/server.py` | `openai_agents_impl/server.py` |

### 样例输入（验收）

| 输入 | 期望情感 | 期望回应方向 |
|------|----------|--------------|
| 「今天面试通过了，好开心！」 | positive | 鼓励、肯定 |
| 「连续加班好累，感觉撑不住了」 | negative | 共情、排解 |
| 「今天下午三点开会」 | neutral | 简短确认式陪伴 |

---

## 11. Docker / 阿里云镜像（OpenAI Agents 版）

Agents 版已提供容器化文件，流程见 **[DOCKER.md](./DOCKER.md)**。

```bash
# 本地构建
docker build -t sentiment-agents:v1 .

# 推阿里云 ACR（先改命名空间/仓库名，并 docker login）
docker tag sentiment-agents:v1 registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-agents:v1
docker push registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-agents:v1
```

相关文件：`Dockerfile`、`requirements-agents.txt`、`.dockerignore`、`build-push-acr.ps1`。

---

## 12. 非目标（本期不做）

- 多轮会话记忆 / 用户画像  
- 鉴权、限流、持久化  
- 复杂沙盒或外部工具调用  
- 生产级部署  

本期聚焦：**把「双进程串联 + 前后端」在两套框架里各实现一遍，便于课堂对比 Reasoning / Agent 编排差异。**
