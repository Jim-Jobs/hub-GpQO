# CLAUDE.md —— 情感陪伴 AI 微服务系统

> 本文件是 Claude Code 在本项目中的**唯一权威工作规范**，由 `情感识别.md` 提炼而来。
> 若本文件与 `情感识别.md` 冲突，以 `情感识别.md` 为准，并当场向用户指出冲突。

## 1. 项目概述

情感陪伴 AI 应用，前后端分离的微服务架构。

业务链路：用户输入 → 情感二分类（`positive` / `negative`）→ 正向给鼓励话术，负向给排解话术。
底层引擎有两种，由前端下拉框选择：**LangGraph** 与 **OpenAI-Agents**。
共三个独立部分：1 个前端页面 + 2 个独立后端微服务。

## 2. 拓扑与通信契约

| 部分 | 框架 | 地址 | 端口 |
|---|---|---|---|
| 后端 A `langgraph_service/` | LangGraph | `127.0.0.1:8000` | 8000 |
| 后端 B `agents_service/` | OpenAI-Agents SDK | `127.0.0.1:8001` | 8001 |
| 前端 `frontend/` | 纯 HTML/CSS/JS | 本地打开 | — |

**API 契约（两端必须完全一致）**

- 请求：`POST /chat`，body `{"user_input": "用户输入的文本"}`
- 响应：`{"sentiment": "positive" | "negative", "response": "AI生成的话术"}`

**技术栈约束**

- 后端统一 **FastAPI**。
- 统一 **同步调用**：禁止 `async def`、禁止 `await`、接口函数用普通 `def`。
- 必须挂载 `CORSMiddleware`，允许前端跨域访问。

## 3. 密钥管理（红线）

- **绝对禁止硬编码任何 API Key**。
- 两个后端目录下各自放一份 `.env`：

```env
SILICONFLOW_API_KEY=your_api_key_here
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V4-Flash
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
```

- 代码中统一用 `python-dotenv` 的 `load_dotenv()` 加载，再通过 `os.getenv()` 读取。
- 模型名、Base URL 也从环境变量读取；`SILICONFLOW_MODEL` 缺省时回退到 `deepseek-ai/DeepSeek-V4-Flash`。
- Key 缺失时给出清晰的中文报错提示，不要静默失败。
- 额外提供 `.env.example`（占位值），`.env` 填写真实 Key，不要提交真实 Key。

## 4. 后端 A：LangGraph 服务

```
langgraph_service/
├── schemas.py         # 契约层
├── nodes.py           # 逻辑层
├── workflow.py        # 编排层
├── main.py            # 网络层
├── requirements.txt
└── .env / .env.example
```

### schemas.py —— 契约层
- `ChatRequest`（`user_input: str`）、`ChatResponse`（`sentiment`、`response`）Pydantic 模型。
- `AgentState(TypedDict)`，字段：`user_input`、`sentiment`、`response`（用 `total=False` 以便部分节点只返回部分字段）。

### nodes.py —— 逻辑层（唯一调用大模型的地方）
- 只允许在以下三个函数中发起模型请求：
  - `analyze_sentiment(state)`：判断 `positive` / `negative`，返回 `{"sentiment": ...}`。
  - `generate_encouragement(state)`：读 `user_input` 生成鼓励话术，返回 `{"response": ...}`。
  - `generate_console(state)`：读 `user_input` 生成排解话术，返回 `{"response": ...}`。
- 使用 `langchain_openai.ChatOpenAI`，注入 SiliconFlow 的 `base_url` / `api_key` / `model`。
  - **必须显式传 `http_client=openai.DefaultHttpx2Client()`**（存在则该类）：langchain-openai 默认自建 `httpx.Client`，与 openai 3.x（基于 httpx2）的 transport 类型不匹配，会抛 `AssertionError`。
- 情感识别需容错：模型输出可能带多余文字，解析后若无法判定，默认归为 `negative`（保守兜底）并注明。
- 中文注释需标注「进程 1（识别）」与「进程 2（生成）」。

### workflow.py —— 编排层
- `StateGraph(AgentState)`，添加上述三个节点。
- `add_conditional_edges` 实现路由：`sentiment == "positive"` → 鼓励节点，否则 → 排解节点。
- 编译后导出全局变量 `app_graph`。
- 中文注释需标注「条件路由位置」。

### main.py —— 网络层
- 初始化 FastAPI，挂载 CORS。
- `@app.post("/chat")`（普通 `def`），内部 `app_graph.invoke({"user_input": ...})`，返回 `ChatResponse`。
- `uvicorn.run(..., host="127.0.0.1", port=8000)`。

## 5. 后端 B：OpenAI-Agents 服务

```
agents_service/
├── schemas.py         # 契约层
├── local_agents.py    # Agent 定义层（原规范写 agents.py，改名以避开与官方 agents SDK 同名冲突）
├── workflow.py        # 编排层
├── main.py            # 网络层
├── requirements.txt
└── .env / .env.example
```

> **文件名偏离说明**：规范原写 `agents.py`，但它与官方 `agents` SDK 包同名，同目录下 `from agents import ...`
> 会解析到自身导致循环导入。已按用户要求改名为 `local_agents.py`，**禁止再用 sys.path / importlib 动态加载**。

### schemas.py —— 契约层
- 与 LangGraph 版相同的 `ChatRequest` / `ChatResponse`。
- 另定义 `SentimentOutput(BaseModel)`，字段 `sentiment: Literal["positive", "negative"]`，用于分类 Agent 的结构化输出。

### local_agents.py —— Agent 定义层
- 必须引入：`from agents import set_default_openai_api, set_tracing_disabled, Agent, OpenAIChatCompletionsModel`。
- 模块顶部执行：`set_tracing_disabled(True)`、`set_default_openai_api("chat_completions")`。
- 实例化 SiliconFlow 兼容的 `openai.AsyncOpenAI` Client（`base_url` + `api_key` 来自环境变量），封装为 `OpenAIChatCompletionsModel`。
  - **必须用 `AsyncOpenAI`**：`Runner.run_sync` 内部驱动事件循环，传同步 `OpenAI` 会报 "can't be used in 'await' expression"。对外调用仍是同步的 `Runner.run_sync`。
- `workflow.py` 正常 `from local_agents import ...` 即可；`Runner` 从 `agents` 导入。
- 三个独立 Agent，模型均指向 `deepseek-ai/DeepSeek-V4-Flash`：
  - `sentiment_agent`：情感识别，`output_type=SentimentOutput`。
  - `encourage_agent`：鼓励话术，普通文本输出。
  - `console_agent`：排解话术，普通文本输出。
- 每个 Agent 需写清中文 instructions。

### workflow.py —— 编排层
- **不使用状态图，也不使用 Agent Handoff**。
- 普通同步函数 `run_workflow(user_input: str) -> ChatResponse`：
  1. `Runner.run_sync(sentiment_agent, ...)` → `final_output.sentiment`。
  2. Python 原生 `if/else` 路由：positive → `encourage_agent`，negative → `console_agent`，均用 `Runner.run_sync`。
  3. 返回 `ChatResponse`。
- 中文注释需标注「进程 1（识别）」「进程 2（生成）」「条件路由位置」。

### main.py —— 网络层
- 初始化 FastAPI，挂载 CORS。
- `@app.post("/chat")`（普通 `def`），内部调用 `run_workflow`。
- `uvicorn.run(..., host="127.0.0.1", port=8001)`。

## 6. 前端规范

- 纯 HTML + 原生 CSS + 原生 JS，**不使用任何前端框架**。
- 组件：引擎下拉框（`LangGraph` / `OpenAI-Agents`）、文本输入框、发送按钮。
- 发送逻辑：
  - LangGraph → `POST http://127.0.0.1:8000/chat`
  - OpenAI-Agents → `POST http://127.0.0.1:8001/chat`
  - 请求头 `Content-Type: application/json`，body 为 `{"user_input": ...}`。
- 收到响应后渲染情感倾向与 AI 回复；请求中显示加载态，失败时给出中文错误提示。

## 7. requirements.txt（两个服务都要包含）

```
fastapi
uvicorn
pydantic
python-dotenv
langgraph
langchain-openai
openai-agents
```

（`openai` 是 `openai-agents` 的依赖，会被自动安装；如需要也可显式列出。）

## 8. 代码风格与注释要求

- 代码整洁、生产级风格；预留读取 `.env` 的接口（配置集中在一处读取，不散落）。
- **清晰的中文注释**，重点标注：
  - 哪里对应「进程 1（识别）」
  - 哪里对应「进程 2（生成）」
  - 条件路由在哪里
- 严禁把代码揉进单一文件，严格按目录规范拆分。
- 模块间依赖方向：`main → workflow → nodes/agents → schemas`；`nodes.py` / `agents.py` 各自独立配置模型。

## 9. 开发执行顺序（当前阶段）

1. ~~阅读规范，产出 CLAUDE.md~~（已完成，等待用户确认）
2. **用户确认 CLAUDE.md 后**，先生成 `langgraph_service/` 与 `agents_service/` 全部后端代码，**暂不生成前端**。
3. 两个服务各自提供启动命令说明（`uvicorn main:app --host 127.0.0.1 --port 8000` / `8001`）。
4. 后端完成并经用户确认后，再写前端 HTML。

## 10. 禁止事项

- 禁止硬编码 API Key、模型名、Base URL（均走环境变量）。
- 禁止使用 `async/await`。
- 禁止在 `nodes.py` / 三个指定函数之外调用大模型（LangGraph 版）。
- 禁止在 agents 版使用 Agent Handoff 或状态图路由。
- 禁止使用前端框架。
- 禁止在用户确认 CLAUDE.md 前写业务代码。
- 禁止在用户未要求时生成前端。
