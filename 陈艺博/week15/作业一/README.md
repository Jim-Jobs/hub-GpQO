# 作业一：情感识别与回应

分别使用 LangGraph 和 OpenAI Agents SDK 实现同一条链路：前端输入 → 后端接收 → 情感识别 → 按情感分支生成回复 → 前端展示。

本项目中的“进程1/进程2”实现为两个独立的逻辑模块（识别模块、回应模块）。后端和前端可以分别运行。项目提供可在浏览器访问的网页前端，也保留命令行前端。情感类别包括正向、负向和中性：正向进入鼓励分支，负向及中性进入理解与排解分支。

## 目录

```text
app/
  backend.py       FastAPI 后端，编排并暴露两种实现
  frontend.py      命令行前端
  static/index.html 浏览器网页前端
  langgraph_flow.py LangGraph 状态图实现
  agents_flow.py   OpenAI Agents SDK 双 Agent 实现
  schemas.py       请求、响应及结构化情感结果
```

## 安装与配置

需要 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

编辑 `.env`，至少填写有效的 `OPENAI_API_KEY`。默认模型为 `gpt-4o-mini`，也可以设置 `OPENAI_MODEL`。如果使用兼容 OpenAI API 的服务，可设置 `OPENAI_BASE_URL`。

## 启动

终端一：启动后端（后端同时托管网页前端）。

```powershell
uvicorn app.backend:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000` 即可使用网页前端；可在页面中切换 LangGraph 或 OpenAI Agents SDK。也可以在终端二启动命令行前端：

```powershell
python -m app.frontend --framework langgraph
```

```powershell
python -m app.frontend --framework agents
```

输入中文文本即可查看识别类别、判断依据和最终回复；输入 `exit` 退出。也可以打开 `http://127.0.0.1:8000/docs` 使用后端接口。

## 接口

两个接口的请求体相同：`{"text": "今天完成了目标，我很开心"}`。

- `POST /api/langgraph`：由 LangGraph 后端处理。
- `POST /api/agents`：由 OpenAI Agents SDK 后端处理。
- `GET /health`：健康检查。

响应包含 `framework`、`emotion`、`reason`、`reply`。识别模块使用结构化输出；回应模块仅在识别结束后运行。LangGraph 通过状态节点和条件边分流；Agents 版本通过 Runner 先运行识别 Agent，再依据结果运行鼓励或排解 Agent。

## 验收示例

- 正向输入：“我坚持练习一个月，终于跑完了五公里。”预期：`positive`，走鼓励回复。
- 负向输入：“最近事情堆在一起，我觉得很挫败。”预期：`negative`，走理解和排解回复。
- 中性输入：“我今天坐公交去上班。”预期：`neutral`，走理解和排解回复。

实际分类由模型完成，结果可能受上下文影响。运行模型调用需要可用的 API 密钥及网络连接。
