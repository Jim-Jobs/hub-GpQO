# week15 作业一 · langgraph 实现（in-memory graph orchestration）

> 情感识别→情感回应 多进程 Pipeline 的 langgraph 复刻套：服务层把
> openai-agents 的 `Agent`/`Runner` 换成 **StateGraph**（单节点图），编排层
> 保持 **langgraph 原生图编排**（直连、零 Temporal 依赖）。openai-agents +
> Temporal 对照实现见 [`../openai-agents/`](../openai-agents/readme.md)。

## 作业原文

> 进程1，接受请求进行情感识别；进程2，得到识别结果后，正向则鼓励，负面则排解。后端进程：接受前端输入，串联进程1和进程2。前端进程：用户输入。

## 架构

```
前端(5174) ──POST /api/chat (SSE)──▶ 网关 8101(本身是 StateGraph + AsyncSqliteSaver)
                                        │ httpx
                              ┌─────────┴──────────┐
                              ▼                    ▼
                       进程1 8102 /analyze    进程2 8103 /respond(SSE 流转发)
                       StateGraph 单节点:     StateGraph 单节点:
                       with_structured_       astream(stream_mode="messages")
                       output(SentimentResult)
```

| 组件 | 目录 | 端口 | 说明 |
|---|---|---|---|
| 前端 | `frontend/` | 5174 | 与 openai-agents 套同壳（localStorage 键区分），proxy → 8101 |
| 网关 | `gateway/` | 8101 | **网关本身是 StateGraph**：analyze 节点 → 条件边 → respond 节点 |
| 进程1 | `sentiment-service/` | 8102 | 单节点图 + `chat_model.with_structured_output(SentimentResult)` |
| 进程2 | `respond-service/` | 8103 | 单节点图 + `astream(stream_mode="messages")` token 流 |

**网关图**（`gateway/main.py`）：

```
START → analyze（httpx 调进程1）→ route_by_label（条件边）→ respond（httpx 流式调
进程2，节点内 get_stream_writer() 把远端 token 写入 custom stream）→ END
```

端点以 `graph.astream(..., stream_mode=["custom","updates"])` 双模式消费：
`updates` 的 analyze 节点产出 → `sentiment` 事件；`custom` 的 token → `delta` 事件。

**复刻对照**（服务层框架对仗）：

| 层 | openai-agents 套 | 本套（langgraph） |
|---|---|---|
| 进程1 | `Agent(output_type=SentimentResult)` + `Runner.run` | StateGraph 单节点 + `with_structured_output(SentimentResult)` |
| 进程2 | 三角色 prompt + `Runner.run_streamed` | 单节点图 + `astream(stream_mode="messages")` |
| 编排 | Temporal workflow（durable） | StateGraph 直连（in-memory graph） |
| 会话 | 网关 SQLiteSession | `AsyncSqliteSaver` checkpointer + thread_id（week13 模式） |

## 进程间 HTTP 契约（与 openai-agents 套完全一致 → 可交叉替换）

```
进程1 POST /analyze
  请求: {"text": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
  响应: {"label": "positive"|"negative"|"neutral", "reason": "一句话依据"}

进程2 POST /respond（响应为 SSE 流）
  请求: {"text": "...", "label": "...", "reason": "...", "history": [...]}
  响应: data: {"delta": "token"} 逐条，结尾恰好一条 data: {"done": true}

各服务 GET /health → {"status": "ok"}
```

契约定义：`contracts.py`（与 openai-agents 套同一份定义的复制）。
交叉替换验证：openai-agents 套 worker 以
`SENTIMENT_URL=:8102 RESPOND_URL=:8103` 跑通端到端（见其 README 演示方法）。

## SSE 事件协议（网关 → 前端，与 openai-agents 套对齐）

| event | data | 时机 |
|---|---|---|
| `meta` | `{thread_id}` | 连接建立，前端存 localStorage |
| `sentiment` | `{label, reason}` | analyze 节点完成（updates 模式产出） |
| `delta` | `{text}` | respond 节点每 token（custom stream） |
| `done` | `{}` | 回应结束，checkpoint 已落 |
| `error` | `{message}` | 任一下游失败/超时/断流，降级收尾（不出 5xx） |

## 启动

```bash
./scripts/start_all.sh   # 进程1 → 进程2 → 网关 → 前端（health 轮询就绪）
# 前端 http://127.0.0.1:5174 · 网关 :8101
./scripts/stop_all.sh    # 一键停止
```

零 Temporal 依赖；凭据在项目根 `.env`（向上查找）。环境变量：
`SENTIMENT_URL`/`RESPOND_URL`（默认 :8102/:8103，网关）。

## 测试

```bash
cd langgraph && /opt/miniconda3/envs/py312/bin/python -m pytest tests/ -v
```

- `test_services.py`：进程1/2 单测（LLM 全 mock：`graph.ainvoke` 固定 label /
  假图 `astream` 假 token chunk 流），断言契约与 SSE 序列；
- `test_gateway.py`：网关集成测（httpx mock 两个下游 FastAPI 假服务）——
  SSE 事件序列、history 经 checkpointer 传递（第二轮 analyze 收到第一轮
  user/assistant）、error 降级两例（进程1 5xx → 无 delta 直接 error；
  进程2 断流 → 已收 delta 保留 + error 收尾）。

ruff：`ruff check langgraph`（全绿）。

## 截图索引（screenshots/）

| 编号 | 内容 |
|---|---|
| 01-前端对话-正向鼓励.png | 正向输入 → 开心徽标 + 鼓励式回应（打字机流式） |
| 02-前端对话-负向排解.png | 负向输入 → 低落徽标 + 共情排解式回应 |
| 03-error降级.png | 手动停掉进程2 → sentiment 后 error 红条，不出 5xx |

两套并排对比与 Temporal UI 执行链截图见
[`../openai-agents/screenshots/`](../openai-agents/screenshots/)（04/05）。

## 踩坑记录

1. **`astream` + custom stream 写法先小实验再落**：节点内 `get_stream_writer()`
   写 dict 事件，端点用 `stream_mode=["custom","updates"]` 双模式消费
   （元组 `(mode, chunk)`）——单 `custom` 模式拿不到 analyze 节点的
   label/reason，单 `updates` 拿不到 token。
2. **messages 状态必须 `add_messages` reducer**：`Annotated[list, add_messages]`
   不写的话每轮输入直接覆盖 state，多轮历史丢失（第二版才补上，多轮冒烟暴露）。
3. **`stream_mode="messages"` 的产出形态**：`(AIMessageChunk, metadata)` 元组，
   `chunk.content` 只含正文 token——qwen 推理模型的 reasoning 走
   `additional_kwargs`，天然过滤，比 openai-agents 版省一层判断。
4. **httpx mock 劫持测试 client**：monkeypatch 全局 `httpx.AsyncClient` 后，
   测试自建的 client 也被换掉（404）——factory 里放行带 `transport` 的调用。
5. **两个 `main.py` 同名**：测试用 `importlib.util.spec_from_file_location`
   以不同模块名显式加载各服务目录下的 main.py。
6. **节点内普通 `ainvoke` 即可透出 token**：不需要在节点里用流式 API——
   "messages" 模式经 langchain callback 拦截 LLM token，节点保持命令式写法。
