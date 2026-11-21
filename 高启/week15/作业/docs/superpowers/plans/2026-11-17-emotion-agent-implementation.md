# 情绪识别双框架实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** 按已确认流程实现两个独立的课程演示项目，使用相同业务数据契约、测试集和评测输出。

**Architecture:** 前端提交单轮请求，API 返回 request ID；识别和回应两个容器经 Redis Streams 交接；前端通过 SSE 接收进度和最终结果。LangGraph 侧由 `agents.py` 定义角色节点、`workflow.py` 定义 LangGraph；OpenAI Agents 侧由 `agents.py` 定义两个独立 Agent。提示词都放在 `prompts.py`。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、LangGraph、LangChain ChatOpenAI、OpenAI Agents SDK、Redis Streams、原生 HTML/CSS/JavaScript、Docker Compose。

**Spec:** `../../../README.md` 和两个子项目 README。

## Global Constraints

- 两个项目只共享 `test/` 中的合成标注数据和评测格式；业务 API、Redis 和 SSE 字段完全一致。
- `.env` 必须配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。
- 单轮处理，不使用会话记忆；第一版不接工具。
- 每个 Python 函数使用中文文档字符串，写明用途、Args、Returns 和适用时的 Raises。
- 提示词放独立 `backend/prompts.py`，Agent 定义放独立 `backend/agent.py`，流程编排放独立模块。
- AgentLoop 原文采集、脱敏、保留期和访问权限尚未确认；不得擅自启用外部内容采集。

## Review Focus

- 情感输出不在三类枚举内或格式损坏：拒绝或转换为明确失败，不静默伪造标签。
- SSE 订阅晚于工作进程状态发布：按 Redis Stream 游标回放请求进度。
- Redis/模型中断：请求失败事件包含阶段和安全错误信息，Redis 消费消息按既定策略确认。
- 同时处理多个请求：所有结果与事件均用 request ID 隔离。
- 测试集和运行记录：只使用合成文本，不存 API Key；两框架输出可以按 case ID 对比。

---

### Task 1: 记录 Agent 文件职责与共同执行流程

**Files:**
- Modify: `README.md`
- Modify: `langgraph/README.md`
- Modify: `openai-agents/README.md`

- [ ] 在两份子项目 README 说明 `agent.py`、`prompts.py`、`workflow.py` 各自职责，不写伪代码。
- [ ] 记录 LangGraph 手写 StateGraph 的 Agent 节点与两个 Redis 工作进程边界。
- [ ] 标注 AgentLoop 数据采集未定，本文本不代表已接入。

### Task 2: 定义共同数据契约和合成评测集

**Files:**
- Create: `test/data/cases.jsonl`（30 条合成标注样例）
- Create: `test/schemas.py`
- Create: `test/evaluate.py`
- Create: `test/README.md`

**数据契约:** `ChatRequest`、`ChatAccepted`、`RedisChatJob`、`RedisSentimentResult`、`ChatResult`、进度/完成/失败 SSE 判别联合；测试结果记录分类标签、回复、输出格式有效率、延迟、Token 和人工回复评分。

- [ ] 先为分类解析、事件联合和评分校验编写失败测试并确认失败。
- [ ] 实现共同 Pydantic 数据类型和评测计算（Accuracy、Macro-F1、格式通过率及人工评分汇总）。
- [ ] 写入 30 条覆盖正向、负向、中性、混合/模糊语气的合成中文样例，人工标签可读；回复不设唯一标准答案。
- [ ] 运行共同 schema 与评测测试。

### Task 3: LangGraph 项目

**Files:**
- Create/Modify: `langgraph/backend/{schemas.py,prompts.py,agent.py,workflow.py,workers.py,api.py}`
- Modify: `langgraph/frontend/index.html`
- Modify: `langgraph/docker-compose.yml`、Dockerfiles、依赖和 README
- Create: `langgraph/tests/`

- [ ] 先写并运行 schema、Agent 适配、工作流及 SSE 事件测试，观察预期失败。
- [ ] 以 StateGraph 描述进程内步骤；Agent 节点及模型适配放 `agent.py`，提示词独立存放。
- [ ] 通过 Redis Streams 在两个工作进程之间交接共同消息，SSE 回放各阶段状态。
- [ ] 执行测试、语法检查和 Compose 配置检查。

### Task 4: OpenAI Agents 项目

**Files:**
- Create/Modify: `openai-agents/backend/{schemas.py,prompts.py,agent.py,workflow.py,workers.py,api.py}`
- Modify: `openai-agents/frontend/index.html`
- Modify: `openai-agents/docker-compose.yml`、Dockerfiles、依赖和 README
- Create: `openai-agents/tests/`

- [ ] 先写并运行 schema、Agent 输出适配、工作流及 SSE 事件测试，观察预期失败。
- [ ] 分别创建情感识别 Agent 和情绪回应 Agent；提示词与 Agent 定义分开。
- [ ] 将 SDK 输出归一化为共同 Pydantic 业务模型，通过 Redis Streams 交接。
- [ ] 执行测试、语法检查和 Compose 配置检查。

### Task 5: 集成验收与部署说明

- [ ] 两套 Compose 均可完成静态配置校验；记录 Docker 镜像构建是否受外部网络影响。
- [ ] 用相同 30 条数据分别生成两套结果记录，保留同一评分 Schema。
- [ ] 更新 README，说明启动步骤、阶段状态、评测方法和未完成的 AgentLoop 接入决策。
- [ ] 不向阿里云创建资源或上传数据；云端部署目标与 AgentLoop 数据边界先标记为待决。
