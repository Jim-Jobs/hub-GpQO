# week15 作业一 · openai-agents 实现（Temporal durable orchestration）

> 情感识别→情感回应 多进程 Pipeline 的 openai-agents 套：服务层用
> openai-agents SDK（`Agent` + `Runner`），编排层用 **Temporal workflow**
> 持久化编排（durable orchestration）。langgraph 对照实现见
> [`../langgraph/`](../langgraph/readme.md)（in-memory graph orchestration）。

## 作业原文

> 进程1，接受请求进行情感识别；进程2，得到识别结果后，正向则鼓励，负面则排解。后端进程：接受前端输入，串联进程1和进程2。前端进程：用户输入。

## 架构

```
前端(5173) ──POST /api/chat (SSE)──▶ 网关 8001(FastAPI + Temporal client + SQLiteSession)
                                        │ ① 取会话历史 ② start_workflow + 订阅流
                                        ▼
                                   Temporal server(:7233, Web UI :8233, dev 单进程内嵌 SQLite)
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

| 组件 | 目录 | 端口 | 说明 |
|---|---|---|---|
| 前端 | `frontend/` | 5173 | week13 Vue3 整壳复用（SSE 解析器不动），打字机+情感徽标 |
| 网关 | `gateway/` | 8001 | Temporal client + SQLiteSession（按 thread_id 持有历史）+ SSE 下发 |
| Worker | `worker/` | —（无 HTTP） | `EmotionPipelineWorkflow` + analyze/respond 两 activity |
| 进程1 | `sentiment-service/` | 8002 | `Agent(name="情感识别", output_type=SentimentResult)` + `Runner.run` |
| 进程2 | `respond-service/` | 8003 | 三角色 prompt + `Runner.run_streamed` token 流 |
| Temporal | —（brew CLI） | 7233 / 8233 | dev server（内嵌 SQLite）+ Web UI |

**一轮对话 = 一次 workflow 执行**：`workflow_id = emotion-{thread_id}-t{轮次}-{随机}`，
run_id 与前端 thread_id 可追溯；Temporal UI（:8233）可见每轮的完整事件历史
（analyze/respond activity 完成记录、重试记录）。

## 进程间 HTTP 契约（与 langgraph 套完全一致 → 可交叉替换）

```
进程1 POST /analyze
  请求: {"text": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
  响应: {"label": "positive"|"negative"|"neutral", "reason": "一句话依据"}

进程2 POST /respond（响应为 SSE 流）
  请求: {"text": "...", "label": "...", "reason": "...", "history": [...]}
  响应: data: {"delta": "token"} 逐条，结尾恰好一条 data: {"done": true}

各服务 GET /health → {"status": "ok"}
```

契约定义：`contracts.py`（pydantic，两套共享同一定义、各自复制一份）。

## SSE 事件协议（网关 → 前端）

| event | data | 时机 |
|---|---|---|
| `meta` | `{thread_id}` | 连接建立，前端存 localStorage |
| `sentiment` | `{label, reason}` | analyze activity 返回后（workflow 发布到流） |
| `delta` | `{text}` | respond activity 每 token 批次（经 Workflow Streams） |
| `done` | `{}` | 回应结束，历史已写回 |
| `error` | `{message}` | 任一下游失败/超时/断流，降级收尾（不出 5xx） |

## Temporal 编排要点（durable-orchestration）

- **Workflow Streams**（`temporalio.contrib.workflow_streams`，实验特性）：
  respond activity 内 `WorkflowStreamClient.from_within_activity(batch_interval=200ms)`
  逐批 publish token；sentiment 事件由 workflow 在两 activity 之间直接
  `stream.topic("events").publish(...)`（durable log offset 保证先于全部 delta）；
  网关 `WorkflowStreamClient.create(...).subscribe(from_offset=0)`——durable log
  回放，订阅晚于发布不丢事件。
- **重试/超时**（官方建议）：analyze `RetryPolicy(maximum_attempts=3)` +
  `schedule_to_close_timeout=60s`；respond `start_to_close_timeout=120s` +
  `heartbeat_timeout=30s`、attempts=2（逐 delta heartbeat 感知取消）；下游
  4xx/契约非法抛 `PermanentHttpError` 列入 `non_retryable_error_types`；
  OpenAI 客户端 `max_retries=0`（重试统一交给 Temporal）。
- **降级**：断流/重试耗尽 → workflow 失败 → 网关展开异常链以 `error` 事件收尾。

## 启动

```bash
# 一键启动（幂等）：temporal dev server → 进程1/2 → worker → 网关 → 前端
./scripts/start_all.sh
# 前端 http://127.0.0.1:5173 · 网关 :8001 · Temporal UI http://127.0.0.1:8233

./scripts/stop_all.sh   # 一键停止
```

前置：`brew install temporal`（CLI 1.10.1）+ `pip install temporalio==1.30.0`；
凭据在项目根 `.env`（`API_KEY`/`BASE_URL`/`BASE_MODEL`，向上查找）。

环境变量：`SENTIMENT_URL`/`RESPOND_URL`（默认 :8002/:8003，worker 与网关）、
`TEMPORAL_ADDRESS`（默认 127.0.0.1:7233）、`TASK_QUEUE`（默认 emotion-oa）。

## 测试

```bash
cd openai-agents && /opt/miniconda3/envs/py312/bin/python -m pytest tests/ -v
```

- `test_services.py`：进程1/2 单测（LLM 全 mock：固定 label / 假 token 流），
  断言 API 契约与 SSE 序列（delta 拼接完整性、reasoning 过滤、done 恰好一条）；
- `test_activities.py`：activity 单测（httpx mock 假下游，含 4xx
  non-retryable / 5xx 瞬时 / SSE 断流剧本 + FakeStreamClient 记录 publish）；
- `test_workflow.py`：`temporalio.testing.WorkflowEnvironment`（time-skipping）
  验证编排顺序（analyze 先于 respond）与 analyze 瞬时失败自动重试。

ruff：`ruff check openai-agents`（全绿）。

## 截图索引（screenshots/）

| 编号 | 内容 |
|---|---|
| 01-前端对话-正向鼓励.png | 正向输入 → 开心徽标 + 鼓励式回应（打字机流式） |
| 02-前端对话-负向排解.png | 负向输入 → 低落徽标 + 共情排解式回应 |
| 03-error降级.png | 手动停掉进程2 → sentiment 后 error 红条，不出 5xx |
| 04-TemporalUI执行链.png | Web UI :8233 中一轮 workflow 的事件历史（analyze/respond） |
| 05-两套并排对比.png | openai-agents 套（本套）与 langgraph 套并排同题对话 |

## 交叉替换演示（契约统一的证明）

两套实现的进程1/2 契约完全一致，把本套 worker 指向 langgraph 套服务即可
端到端运行（已验证，见 tasks 6.3）：

```bash
pkill -f worker/main.py
cd openai-agents
SENTIMENT_URL=http://127.0.0.1:8102 RESPOND_URL=http://127.0.0.1:8103 \
  /opt/miniconda3/envs/py312/bin/python worker/main.py
# 此时网关（Temporal 编排）串联的是 langgraph 版进程1/2，前端体验不变
```

## 踩坑记录

1. **openai-agents 0.20.0 自定义 base_url**：必须
   `set_default_openai_client(AsyncOpenAI(...))` + `set_tracing_disabled(True)`，
   否则 tracing 上报报错（任务 1.3 最小脚本先行验证）。
   **版本锁定 0.20.0 不升级**：本次全部行为按该版本验证。
2. **qwen3.8-max 是推理模型**：`Runner.run_streamed` 的事件流里
   `response.reasoning_text.delta`（思考过程）与 `response.output_text.delta`
   （正文）混排，只透传后者，否则用户会看到模型内心戏。
3. **事件形态**：0.20.0 流式事件类型是 `raw_response_event`（非直觉的
   raw_model_stream_event），`event.data.type == "response.output_text.delta"`
   取 token——先跑小实验 dump 全部事件再写正式代码。
4. **temporal CLI 参数名**：`temporal server start-dev` 持久化库是
   `--db-filename`（不是 `--sqlite-dev-db-file`）；7233 是 gRPC 端口，
   HTTP curl 探活用 `temporal operator cluster health`。
5. **Workflow Streams 真实 API 与任务书差异**：`from_within_activity()` 存在但
   签名经 `inspect` 实探（`batch_interval` 默认 2s，显式传 200ms）；
   `force_flush` 只在客户端 TopicHandle.publish 上有，workflow 侧
   `WorkflowTopicHandle.publish(value)` 无此参数（sentiment 单事件无所谓批次）。
6. **workflow sandbox 限制**：workflow 模块顶层不能跑 `pathlib.Path.resolve`
   （RestrictedWorkflowAccessError）——sys.path 注入移到 worker 入口进程级，
   workflow 文件里只做 `imports_passed_through()`。
7. **SQLiteSession 是 async 接口**：`get_items()`/`add_items()` 都要 await
   （inspect 签名看不出 coroutine，第一版用 to_thread 包同步调用直接炸）。
8. **temporalio 1.30 `start_workflow` 多参数**：位置参数只收一个 `arg`，
   多参数必须 `args=[text, history]` 关键字传入。
9. **重试语义在 worker 侧生效**：事件历史里 ActivityTaskFailed 只出现一次
   （server 端合并 attempt），要看真实重试次数得看 worker 日志
   （"Completing activity as failed (attempt: 2/3)"）。

## 与 langgraph 套的编排哲学对比

| | 本套（openai-agents + Temporal） | langgraph 套 |
|---|---|---|
| 编排 | durable orchestration：事件历史持久化、自动重试、可回放、Web UI 可视化 | in-memory graph orchestration：轻量 StateGraph、条件边、checkpointer |
| 每轮 | 一次 workflow 执行（run_id 可追溯） | 一次图执行（thread_id checkpoint） |
| 流式通路 | respond activity → Workflow Streams（200ms 批次）→ 网关订阅 → SSE | respond 节点 `get_stream_writer()` → custom stream → SSE |
| 重试 | activity RetryPolicy（框架级） | 无框架级重试（失败即降级 error） |
| 会话 | 网关 SQLiteSession（history 作 workflow 参数） | 网关 AsyncSqliteSaver checkpointer（messages 状态累积） |
