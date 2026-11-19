# week15 作业一 · 技术设计文档

> 情感识别→回应 分布式多进程 Pipeline(openai-agents[Temporal 编排] + langgraph 复刻)
>
> 状态:**设计已确认(含 Temporal 定案),待实施** · 2026-11-18

## 1. 作业需求与澄清结论

作业原文:

> 进程1,接受请求进行情感识别;进程2,得到识别结果后,正向则鼓励,负面则排解;后端进程:接受前端输入,串联进程1和进程2;前端进程:用户输入。

需求澄清结论(与用户逐项确认):

| 决策点 | 结论 | 理由 |
|---|---|---|
| "进程"语义 | **真实独立进程**,分布式架构 | 用户明确以学习分布式架构设计为第二目标 |
| 进程拓扑 | 4 进程:前端 / 后端网关 / 进程1 情感识别 / 进程2 情感回应 | 网关串联,下游无状态 |
| 进程间通信 | **HTTP REST + httpx** | 同步请求-响应语义契合;可调试;作业2 dockerfile 打包自然 |
| 情感识别实现 | **LLM 结构化输出** | 契合 agent 框架主题,零额外依赖 |
| 分类粒度 | 三分类 positive / negative / neutral,中性单独回应 | 真实输入大量中性,二分会生硬 |
| 回应策略 | 正向→鼓励,负面→排解,中性→平和回应;**分支在进程2 内部按 label 选角色** | 进程间契约保持简单 |
| 流式范围 | **端到端 token 级流式**(进程2 流式 → 网关 SSE 转发 → 前端打字机) | 分布式经典问题:流式级联,亮点 |
| 会话 | 多轮;进程1/2 **无状态**(网关每次带 history);历史由网关持有 | 分布式无状态服务经典设计 |
| 复刻对仗 | **双层对仗**:服务内部换框架 + 网关编排层换框架 | 复刻学习价值最大 |
| 编排引擎分配(2026-11-18 定案) | openai-agents 套网关 = **Temporal workflow**;langgraph 套网关 = StateGraph 直连(原设计) | OA SDK 无网关级编排概念(原网关只是 httpx),换 Temporal 是升级无损;LG 自带编排,加 Temporal 反被压制;形成"两种编排哲学"对比 |
| 目录组织 | 两个实现各自完整独立(含各自 frontend 副本) | 互不干扰,作业2 只打包 openai-agents 目录 |
| 端口 | openai-agents 8001/8002/8003/5173;langgraph 8101/8102/8103/5174 | 两套可同时运行、并排截图对比 |
| 实施顺序 | 先 openai-agents,后 langgraph 复刻 | 用户既定计划 |

## 2. 技术栈(py312 环境全部已装,零新依赖)

| 组件 | 选型 | 版本 | 备注 |
|---|---|---|---|
| LLM | OpenAI 兼容接口,`邓帅/.env`(API_KEY/BASE_URL/BASE_MODEL) | openai 2.50.0 | pydantic-settings + find_dotenv,week14 模式 |
| 框架 A | openai-agents | 0.20.0 | **不升级**,README 记录版本 |
| 框架 B | langgraph + langgraph-checkpoint-sqlite | 1.2.10 / 3.1.1 | week13 已有使用经验 |
| Web | FastAPI + uvicorn | 0.140 / 0.40 | 三服务同栈 |
| SSE | sse-starlette `EventSourceResponse` | — | week13/14 同款模式 |
| 编排引擎 | temporalio(Temporal Python SDK)+ temporal CLI | 1.30.0(pip 新装)/ CLI(brew 新装) | **仅 openai-agents 套使用**;py3.12 支持 ✓;本地均未装,是仅有的两个新依赖 |
| HTTP 客户端 | httpx(openai SDK 依赖,已随装) | — | 网关调下游、进程2 SSE 接收 |
| 前端 | Vue 3.5 + element-plus + marked + Vite 6 | — | week13 整壳第三次复用;SSE 解析器(fetch+ReadableStream)不动 |
| 包管理 | 后端 pip / 前端 pnpm(需 `~/.nvs/default/bin` 前置 node20) | — | 沿用 week14 习惯 |

## 3. 架构总览

**openai-agents 套(durable orchestration)**

```
前端(5173) ──POST /api/chat (SSE)──▶ 网关 8001(FastAPI + Temporal client + SQLiteSession)
                                        │ ① 取会话历史 ② start_workflow + 订阅流
                                        ▼
                                   Temporal server(:7233,Web UI :8233,dev 单进程内嵌 SQLite)
                                        │ task queue "emotion-oa"
                                        ▼
                                   Worker 进程(Temporal workflow: analyze → respond)
                                        │ activity = httpx
                              ┌─────────┴──────────┐
                              ▼                    ▼
                       进程1 8002 /analyze    进程2 8003 /respond
                       情感识别(无状态,JSON)  情感回应(无状态,SSE 流)
                              │                    │
                              └──{label,reason}────┤
                                                   │ token 经 Workflow Streams(200ms 批次)
                                                   ▼ 网关订阅 → SSE delta → 前端打字机
```

**langgraph 套(in-memory graph orchestration,直连原设计)**

```
前端(5174) ──POST /api/chat (SSE)──▶ 网关 8101(本身是 StateGraph + AsyncSqliteSaver)
                                        │ httpx
                              ┌─────────┴──────────┐
                              ▼                    ▼
                       进程1 8102 /analyze    进程2 8103 /respond(SSE 流转发)
```

时序(两套一致):前端提交 → 网关取历史 → 调进程1 得 `{label, reason}` → 推 `sentiment` 事件 → 调进程2(流式)逐 token 转发 `delta` → 完成后 `done`,网关将本轮 user/assistant 消息写回会话。

## 4. 进程间 API 契约(两个实现统一,复刻基础)

**进程1 情感识别服务**

```
POST /analyze
  请求: {"text": "今天心情真好", "history": [{"role": "user"|"assistant", "content": "..."}]}
  响应: {"label": "positive"|"negative"|"neutral", "reason": "一句话依据"}
GET /health → {"status": "ok"}
```

**进程2 情感回应服务**

```
POST /respond   (响应为 SSE 流)
  请求: {"text": "...", "label": "...", "reason": "...", "history": [...]}
  响应: text/event-stream,data: {"delta": "token"} 逐条,结尾 {"done": true}
GET /health → {"status": "ok"}
```

契约统一的价值:两个实现的进程1/2 理论上可交叉替换(langgraph 网关接 openai-agents 服务),可作为 README 演示点。

## 5. SSE 事件协议(网关 → 前端,沿用 week13/14 栏目)

| event | data | 时机 |
|---|---|---|
| `meta` | `{thread_id}` | 连接建立,前端存 localStorage |
| `sentiment` | `{label, reason}` | 进程1 返回后;前端展示"识别到情绪"徽标 |
| `delta` | `{text}` | 进程2 每 token |
| `done` | `{}` | 回应结束 |
| `error` | `{message}` | 任一下游失败/超时,前端红条提示 |

## 6. 双层对仗设计(复刻核心)

| 层 | openai-agents | langgraph |
|---|---|---|
| 进程1 实现 | `Agent(name="情感识别", output_type=SentimentResult)` + `Runner.run` | `StateGraph` 单节点,节点内 `chat_model.with_structured_output(SentimentResult)` |
| 进程2 实现 | 三角色 prompt 按 label 切换,`Runner.run_streamed` | 单节点图,`astream(stream_mode="messages")` 流式 |
| 网关编排 | **Temporal workflow**(durable):`EmotionPipelineWorkflow` = analyze activity(httpx 调进程1)→ respond activity(httpx 流式调进程2,`Workflow Streams` 200ms 批次 publish token);事件历史可回放,Web UI :8233 可视化 | **网关本身是 StateGraph**:analyze 节点(httpx 调进程1)→ 条件边 → respond 节点(httpx 流式调进程2),节点内 `get_stream_writer()` 把远端 token 写入 custom stream |
| 会话历史 | 网关 `SQLiteSession`(按 thread_id;每轮对话 = 一次 workflow 执行,history 作参数传入) | `AsyncSqliteSaver` checkpointer + thread_id(week13 现成经验) |

叙事:两种编排哲学对比——**durable orchestration**(Temporal:事件历史、自动重试、可回放、UI 可视化)vs **in-memory graph orchestration**(langgraph:轻量图、条件边、checkpointer)。

## 7. 目录结构与端口规划

```
邓帅/week15/作业一/
├── design.md                      # 本文档
├── openai-agents/
│   ├── gateway/                   # 8001:FastAPI + Temporal client + SSE + SQLiteSession
│   ├── worker/                    # Temporal worker(无 HTTP 端口):workflow + 2 activities
│   ├── sentiment-service/         # 8002:进程1
│   ├── respond-service/           # 8003:进程2
│   ├── frontend/                  # 5173:week13 整壳复制改造
│   ├── scripts/start_all.sh       # temporal server(dev)→服务→worker→网关→前端,health 就绪探测
│   └── screenshots/               # 编号截图(含 Temporal UI :8233 执行链)
├── langgraph/                     # 直连原设计:8101(StateGraph 网关)/8102/8103/5174
└── readme.md                      # week15/readme.md 顶部保留作业原文 + 加索引
```

| 进程 | openai-agents | langgraph |
|---|---|---|
| 网关 | 8001 | 8101 |
| 进程1 | 8002 | 8102 |
| 进程2 | 8003 | 8103 |
| Worker | —(连 Temporal :7233) | —(无) |
| Temporal server | :7233(命令行)/ Web UI :8233 | 不使用 |
| 前端 dev | 5173 | 5174 |

vite proxy `/api` → 各自网关端口;CORS 白名单对应前端端口。

## 8. 配置与环境

- 凭据:`邓帅/.env` 向上查找(API_KEY/BASE_URL/BASE_MODEL),pydantic-settings,week14 模式
- 服务发现:网关环境变量 `SENTIMENT_URL`(默认 `http://127.0.0.1:8002`)、`RESPOND_URL`(默认 `:8003`);langgraph 网关默认指向 81xx
- Temporal(仅 openai-agents 套):`TEMPORAL_ADDRESS`(默认 `localhost:7233`)、`TASK_QUEUE`(默认 `emotion-oa`);temporal dev server 由该套 `start_all.sh` 负责拉起,langgraph 套零 Temporal 依赖
- openai-agents 挂自定义 base_url:**必须** `set_default_openai_client(AsyncOpenAI(...))` + `set_tracing_disabled(True)`,否则 tracing 上报报错(已知坑,提前设计)

## 9. 分布式设计学习点(README 亮点章节)

1. **无状态服务**:进程1/2 不落盘,history 由调用方携带 → 可横向扩容(README 说明)
2. **健康检查与就绪探测**:每服务 `/health`;start_all.sh 启动后轮询等待就绪再拉下一个
3. **超时与降级**:httpx `AsyncClient(timeout=30s, connect=5s)`;下游失败 → 网关发 SSE `error` 事件而非 500 白屏
4. **流式级联**:跨两跳的 SSE 转发与背压(进程2 → 网关 → 前端)
5. **durable execution**:Temporal workflow 事件历史、自动重试、执行链可视化(Web UI :8233 截图)——与 langgraph checkpointer 形成两种持久化哲学对照

## 10. 测试策略(沿用 week13 pytest + mock 模式)

- 进程1/2 单测:LLM 全 mock(固定 label / 假 token 流),验证 API 契约
- 网关集成测:httpx mock 两个下游,验证 SSE 事件序列与错误降级
- ruff lint;前端 `pnpm run build` 冒烟

## 11. 实施顺序

1. **契约定稿**:按 §4 落 pydantic schema(两个实现共享定义,各自复制)
2. **openai-agents 全链路**:进程1 → 进程2 → temporal CLI 安装 → worker(workflow + activities + Workflow Streams)→ 网关 → 前端 → 联调 → 截图(含 Temporal UI :8233)
3. **langgraph 复刻**:进程1 → 进程2 → 网关(StateGraph 编排 + checkpointer)→ 前端(改端口)→ 联调 → 截图
4. **文档**:两套 README(作业原文 → 架构图 → SSE 协议表 → 启动命令 → 测试 → 截图表 → 踩坑记录 + 编排哲学对比)+ week15/readme.md 索引

## 12. 风险清单

| 风险 | 应对 |
|---|---|
| openai-agents 0.20.0 对自定义 base_url 的 tracing 报错 | `set_tracing_disabled(True)` + `set_default_openai_client`,§8 已设计 |
| langgraph 网关 astream + custom stream 转发写法不熟 | week13 已用 `astream(stream_mode="updates")`;本次 `messages`/`custom` 模式,先行小实验再落 |
| 跨服务 SSE 转发的断流/半包 | httpx `stream()` + 逐行解析;`sse-starlette` 服务端 yield dict,与 week14 同款 |
| 两套同时运行的端口/进程管理混乱 | start_all.sh + stop_all.sh,日志进各自 logs/ |
| Workflow Streams 为公开预览特性(2026-06 官宣,Python 预览) | 主路走官方 "Stream LLM output" 示例;坑深则降级:respond activity 整段返回 + 网关伪流式(见 §13) |
| temporal CLI / temporalio 均未安装 | `brew install temporal` + `pip install temporalio`(==1.30.0,py3.12 ✓),start_all.sh 自动 start-dev |

## 13. Temporal 细节(调研定稿,来源:docs.temporal.io / temporal.io/blog / PyPI 实查 2026-11)

- **dev server**:`temporal server start-dev --sqlite-dev-db-file <db>` 单进程内嵌 SQLite,server :7233 / Web UI :8233,自动建 default namespace;`brew install temporal` 安装
- **进程关系**:worker 与网关**分进程**(分布式教学价值;官方 asyncio 原生,同进程跑 FastAPI 技术可行但非默认姿势,不采用)
- **workflow**:`@workflow.defn class EmotionPipelineWorkflow`,execute(text, history);每轮对话一次执行,run_id 与 thread_id 关联可追溯;多轮历史仍由网关 SQLiteSession 持有(workflow 不承载对话状态——长驻对话 workflow 属展望,不进主体)
- **流式(Workflow Streams,`temporalio.contrib.workflow_streams`)**:`@workflow.init` 建 `WorkflowStream`;respond activity 内 `WorkflowStreamClient.from_within_activity()` 按 200ms 批次 publish delta;网关 `subscribe()` 后转 SSE;注意 RETRY 事件 + ack 握手 + 首包 force_flush(官方示例套路)
- **重试/超时(官方百科 + 设计模式建议)**:
  - analyze activity:`RetryPolicy(maximum_attempts=3)`,ScheduleToCloseTimeout 限总重试时长(60s 量级)
  - respond activity(流式):`StartToCloseTimeout` 放宽至 120s 量级 + 设 heartbeat_timeout 以感知取消,attempts=2(重试=重新生成的成本)
  - 永久错误(4xx/鉴权/超长上下文)列入 `non_retryable_error_types` 精确匹配
  - OpenAI 客户端 `max_retries=0`,重试统一交给 Temporal
- **测试**:activity 单测(httpx mock);workflow 用 `temporalio.testing.WorkflowEnvironment`(time-skipping)
- **降级路径**:Workflow Streams 坑深 → respond activity 整段返回,网关伪流式逐字下发(体验小降、架构不变),README 记录取舍
- **展望(不进主体,README 可提)**:官方 OpenAI Agents SDK 插件(streaming_topic)与 LangGraph 插件(图节点=Activity)均为公开预览;长驻对话 workflow(Update-with-Start / early-return)
