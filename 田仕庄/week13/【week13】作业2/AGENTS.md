# AGENTS.md — Agent 开发规范

本文档定义本项目中 SQL Agent 的设计规范、节点职责、状态契约与扩展约定。

## 1. Agent 定位

自然语言 → SQL → 执行 → 自然语言答案 的**只读查询 Agent**，运行在 Chinook（SQLite）数据库上，交付形态为命令行（CLI 单次提问 + REPL 交互）与 **Web 对话界面（FastAPI + Vue 3）**。

**入口约定**：**两个入口职责单一，不要混用**——`main.py` 只做 CLI（带问题参数 = 单次提问，不带参数 = REPL），`server.py` 只做 Web 服务（`python server.py`）。`main.py` 不再接受 `--web` 等选项参数：遇到以 `-` 开头的参数直接提示"网页版请用 `python server.py`"并以退出码 2 结束，避免被当成自然语言问题丢给 LLM。

**环境约定**：Python 解释器统一使用 conda `py312` 环境；所有可变配置（LLM / 数据库 / 日志 / 重试 / Web 监听）一律走 `.env`，代码中不硬编码。前端构建需 Node 18+（`web/frontend`，`npm run build`）。

## 2. 状态契约（`sql_agent/state.py`）

所有节点共享 `AgentState`（TypedDict）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `question` | str | 用户自然语言问题（入口必填） |
| `history` | list[(q, a)] | 会话记忆（`operator.add` reducer 累积），generate_answer 每轮追加；配合 checkpointer + thread_id 支持多轮指代 |
| `table_names` | list[str] | 命中的相关表名 |
| `table_schemas` | str | 拼装后的表结构/样例上下文（含行数量级） |
| `sql` | str | 当前生成的 SQL |
| `query_result` | str | 查询结果（Markdown 表格或错误信息） |
| `error` | str | 最近一次执行错误 |
| `retry_count` | int | 已重试次数（上限 3，get_schema 每轮重置） |
| `answer` | str | 最终自然语言回答 |

**规则**：节点只读取需要的字段、只覆写自己负责的字段；禁止在节点内做全局副作用（日志除外）。

## 3. 节点职责（`sql_agent/nodes.py`）

| 节点 | 输入 | 输出 | 失败行为 |
|---|---|---|---|
| `get_schema` | question | table_names, table_schemas, retry_count=0, 评审字段初始化 | 无相关表时返回全部表的简要 schema |
| `write_query` | question, table_schemas, history | sql, **original_sql** | LLM 异常向上抛出 |
| `optimize_query` | sql, question, table_schemas, review_feedback | sql, optimize_count+1 | 改写引入错误由 execute 回退兜底 |
| `review_query` | sql, question, table_schemas | review_verdict, review_feedback | JSON 解析失败按 approve 放行（不阻塞流程） |
| `execute_query` | sql, original_sql | query_result, error | 失败时若改写 SQL ≠ 原 SQL，**一次性回退执行原 SQL**；仍失败写入 error |
| `rewrite_query` | sql, error, table_schemas, question, history | sql, retry_count+1 | 提示词强制逐字核对列名、禁止重复失败 SQL；达到上限 → 致歉回答 |
| `generate_answer` | question, sql, query_result（或 error）, history | answer, history+[(q,a)] | error 非空时不调用 LLM，直接输出失败说明；传入 `on_token` 时流式生成，流式不可用自动回退 |

节点均为**工厂函数**（`make_xxx(llm, parser)`），依赖注入便于测试替换为 Fake 实现。提示词通过 `history_block()` 注入最近 3 轮问答，支持"那前10名呢？"这类指代。**节点进度文案统一维护在 `sql_agent/labels.py`（`NODE_LABELS`），CLI 与 Web 两入口共用，禁止在入口侧复制一份**。

**评审回路开关**：`config.ENABLE_QUERY_REVIEW`（.env，默认 true）。关闭时图退化为 5 节点基础链路（write → execute 直连）。

**改写策略**：`config.OPTIMIZE_STRATEGY`
- `on_revise`（默认）：评审驱动改写，SQL 通过评审则跳过 optimize，happy path 少一次 LLM 调用
- `always`：无条件先改写再评审（兼容旧拓扑，每次多一次调用）

## 4. 路由规则（`sql_agent/graph.py`）

```
START → get_schema → write_query → review_query              （默认 on_revise）
START → get_schema → write_query → optimize_query → review_query   （OPTIMIZE_STRATEGY=always）
review_query --approve--> execute_query
review_query --revise且 optimize_count < MAX_OPTIMIZE_ROUNDS(2)--> optimize_query（带评审意见）→ review_query
review_query --revise且轮次耗尽--> execute_query（强制放行）
execute_query --成功--> generate_answer → END
execute_query --失败且 retry_count < 3--> rewrite_query → execute_query
execute_query --失败且重试耗尽--> generate_answer（输出失败说明）→ END
```

- 改写轮次上限：`MAX_OPTIMIZE_ROUNDS = 2`；执行重试上限：`MAX_RETRIES = 3`（config 中定义）
- revise 回路必须把 `review_feedback` 注入改写提示词；失败重试必须把 `error` 与上一次 `sql` 注入重写提示词，形成纠错闭环
- `execute_query` 首次失败时若 `sql ≠ original_sql`，先回退执行原 SQL（防改写引入错误）
- 两条条件边（`_route_after_review` / `_route_after_execute`）是纯函数，必须可独立单测

## 5. 安全红线

1. **只读**：`execute_sql` 仅接受单条 SELECT/WITH 语句（先剥离注释与字符串字面量再校验，避免 `WHERE x='DELETE'` 误杀）；拒绝 DML/DDL/PRAGMA/ATTACH/多语句
2. **只读连接**：SQLite 以 `sqlite:///file:path?mode=ro&uri=true` URI 打开，纵深防御（写操作直接被数据库层拒绝）
3. **资源限制**：返回行数上限 `MAX_RESULT_ROWS`（默认 50，超出截断并标注）；语句级超时 `QUERY_TIMEOUT`（默认 10s，progress handler 实现，大笛卡尔积等低效 SQL 强制中止）
4. **角色约束**：`generate_answer` 提示词声明只读助手身份，用户请求写操作时明确告知限制，不输出误导性表述
5. LLM key 一律走环境变量（`.env`），**严禁硬编码入库**，**严禁写入日志**

## 6. 性能规范

**基线**：Chinook 数据库 SQL 执行耗时 0.00~0.01s（占比 <1%），瓶颈 100% 在 LLM 往返。因此性能工作只做两件事：**减少调用次数**、**降低单次延迟**。

| 约定 | 要求 |
|---|---|
| 思维链 | 结构化短输出任务（写 SQL / 评审 / 组织回答）必须关闭思维链；`resolve_extra_body` 只对推理模型注入参数，保证跨供应商可移植 |
| 模型选择 | 质量关键路径（write/rewrite）用 `LLM_MODEL`；辅助节点用 `LLM_FAST_MODEL`（可配更轻模型） |
| 调用次数 | 新增节点必须评估"能否按需触发"（参考 `OPTIMIZE_STRATEGY=on_revise`），happy path 的 LLM 调用次数应保持 ≤3 |
| 上下文体积 | schema 上下文只放相关表；样例行数由 `SCHEMA_SAMPLE_ROWS` 控制；单表片段必须缓存 |
| 冷启动 | 热路径禁止导入 pandas / langchain_openai 等重依赖（需 `TYPE_CHECKING` 或函数内延迟导入） |
| 感知延迟 | 回答必须支持流式（`on_token` 回调）；流式不可用时自动回退 `invoke` |
| **Web 首字延迟** | 页面首 token（TTFT）是主要感知指标：`bench_web.py` 实测目标 ≤2s（极速模式）。Web 侧不得新增串行 LLM 调用 |
| **事件粒度** | 流式 token 必须批量下发（后端 40ms 窗口合并，首块立即发）+ 前端按 `requestAnimationFrame` 上屏，禁止"逐字一个事件" |
| **启动预热** | 服务启动时后台预建 schema 缓存与 LLM 连接池（`WEB_WARMUP`），把首次请求的前置开销挪到启动阶段 |
| 回归方式 | 改动后用 `python tools/bench_perf.py`（Agent 侧）与 `python tools/bench_web.py`（Web 侧，建议 `--repeat 2`）对比耗时，并在 TEST_REPORT 记录前后数据 |

## 7. 测试规范（`tests/`，共 101 项）

| 文件 | 数量 | 说明 |
|---|---|---|
| `test_db_parser.py` | 11 | 元数据解析、外键、样例、只读连接、schema 缓存、行数统计、执行超时、Markdown 渲染（离线） |
| `test_safety.py` | 24 | 白名单校验矩阵、写操作拦截、字符串字面量不误杀、行数截断（离线） |
| `test_log.py` | 4 | 文件写入、幂等初始化、命名空间、级别继承（离线） |
| `test_llm_config.py` | 15 | 思维链参数注入/不注入、快慢模型回退、角色映射、SSE 解析、重试与异常（离线） |
| `test_graph.py` | 20 | FakeLLM：基础链路 10 + 评审回路 5 + on_revise 3 + 流式回答 2（离线） |
| `test_web.py` | 24 | Web API：健康检查/新会话/入参校验/SSE 事件协议/多轮记忆/失败路径/静态托管 + 极速模式/评审回路/token 批处理/预热容错 + CORS 跨域/心跳保活/超时释放锁 + **锁幂等释放/泄漏自愈/客户端断开不泄漏** + **问答缓存命中/历史隔离/nocache 开关/健康检查暴露状态**（23 离线）+ 真实 LLM 流式端到端（1 集成） |
| `test_integration.py` | 3 | 真实 LLM 端到端，评审回路开启（`@pytest.mark.integration`） |

- 测试隔离策略（conftest）：`query_review` 标记开启评审；`optimize_always` 标记强制旧拓扑（脚本化响应顺序敏感）；其余单测默认关闭评审；integration 跟随 .env
- 测试基建单一来源：Fake LLM 替身（`FakeLLM` / `FakeStreamLLM`）统一定义在 `tests/fakes.py`，禁止在各测试文件里复制；`parser` 夹具统一用 conftest 的 session 级版本（全 suite 共享一次元数据解析，勿在测试文件里本地覆盖）
- 离线测试（97 项）不依赖真实 LLM：`pytest -m "not integration"`
- 集成测试（4 项）需有效 key：`pytest -m integration`
- marker 在 `pytest.ini` 注册；提交前 `pytest -v` 必须 101 项全绿

## 8. 日志规范（`sql_agent/log.py`）

- 入口处调用一次 `setup_logging()`；各模块统一 `get_logger(__name__)` 取 logger（挂在 `sql_agent.*` 命名空间）
- 双通道：控制台（stderr）+ 滚动文件 `logs/sql_agent.log`（1MB/份，保留 3 份）
- 级别约定：`INFO` 节点关键事件（表命中/SQL 生成/执行成败/路由）；`DEBUG` 明细（结果预览/图构建）；`WARNING` 安全拦截与执行错误
- **严禁**将 API key、完整用户敏感数据写入日志

## 9. Web 模块规范（`server.py` + `web/frontend/`）

**后端（FastAPI，`server.py`）**：
- `create_app(agent=None, static_dir=None)` 工厂：依赖注入与 `build_agent` 同构，测试注入 FakeLLM 图，不 mock 全局
- `POST /api/chat` 返回 SSE 流，事件协议：`node`（节点+耗时）/ `sql` / `result`（Markdown 或错误）/ `token`（回答流式）/ `done`（answer/elapsed/timings，缓存命中时额外带 `cached`/`origin_elapsed`）/ `error`；请求体含 `fast`、`nocache` 开关
- **双模式**：`create_app` 构建两条图——严谨（评审回路，跟随 `.env`）与**极速**（`ENABLE_QUERY_REVIEW=False` 编译，write → execute → answer）。拓扑在编译期决定，故临时改写 `config.ENABLE_QUERY_REVIEW` 后必须还原（`_build_agent_with_token`）
- 流式实现：`agent.stream` 跑在独立 worker 线程，事件经 `queue.Queue` 转发；`on_token` 经 **thread-local 分发器**（`_StreamHub`）绑定到当前请求队列，并发安全
- **token 批量下发**：`_TokenBatcher` 按 40ms 窗口（或 64 字符）合并，首块立即发；worker 结束前必须 `flush()` 且排在 `done` 之前
- **单请求串行锁**：LLM 是唯一瓶颈，忙时返回 409；禁止为 Web 层引入并发复杂度。**客户端中途断开时同步 SSE 生成器的 `finally` 可能永不执行**（Starlette 在线程池迭代，取消无法传导到阻塞的 `queue.get`），因此锁必须用幂等 `_LockGuard` 冗余释放（worker `finally` 必执行 + 生成器 `finally` + `BackgroundTask`），并带超时/未启动自愈换锁；禁止只在生成器里 `release()`
- **问答缓存**：`_AnswerCache`（TTL + LRU，`WEB_CACHE_TTL` / `WEB_CACHE_SIZE`），键 = 问题 + 模式 + **会话历史**（多轮上下文不串答案），只缓存成功且有回答的链路；命中时不占锁、不调 LLM，直接回放 SSE 事件（节点耗时置空、`done` 标 `cached`）。性能基准默认带 `nocache` 绕过缓存
- **CORS 必须放开**：`CORSMiddleware`（`allow_origins=["*"]`）。跨域被浏览器拦截时一律表现为 `TypeError: Failed to fetch`，无法定位；本地只读服务无凭证，放开无风险
- **SSE 心跳**：空闲期每 15s 发 `: ping` 注释帧保活（前端解析时忽略），总超时按**累计空闲**判定（`_EVENT_TIMEOUT`），心跳不重置计时；连接断开或超时后必须释放串行锁
- 会话记忆：MemorySaver + thread_id（`/api/session/new` 发号），多轮指代行为与 CLI 一致
- Web 层不做业务逻辑：只做协议转换（state → SSE 事件），节点行为全部复用 `sql_agent` 包
- 日志：`get_logger("web")` → `sql_agent.web` 命名空间，INFO 记录请求入口/done 耗时/节点明细

**前端（Vue 3 + Vite，`web/frontend/`）**：
- 组件结构：`App.vue`（对话主界面/输入区/欢迎页）+ `components/MessageItem.vue`（助手消息卡片）；`lib/api.js` 封装 SSE（fetch + ReadableStream 解析，EventSource 不支持 POST）
- 构建产物 `dist/` 由 `server.py` 静态托管（`base: './'` 相对路径）；开发用 `npm run dev`，`/api` 代理到 `WEB_PORT`
- **渲染性能**：流式 token 先进缓冲区，由 `requestAnimationFrame` 每帧合并上屏一次（禁止每个 token 直接改响应式变量）；**`done` 事件必须先 flush 缓冲再兜底赋值**，否则 rAF 未触发时完整 answer 会与缓冲 token 叠加、回答出现两遍；自动滚动仅在用户距底部 <160px 时触发，避免打断上滑阅读
- **错误必须可操作**：网络层异常（`Failed to fetch`）与流中断不得原样抛给用户——`api.js` 统一翻译为含排查步骤的中文提示（后端未启动 / 端口不一致 / `file://` 打开）；页面加载即做健康检查，失败时顶部横幅给出指引与"重新检测"按钮
- 结果表格用 `marked` 渲染（内容为服务端 db_parser 生成的可信 Markdown）；答案为纯文本流式拼接
- 修改前端后必须 `npm run build` 重新产出，并在 TEST_REPORT 记录验证结果

**性能约定（延续第 6 节）**：SSE 转发不增加 LLM 调用；Web 层开销应保持 <5ms；前端构建产物 gzip 后应保持 <100KB。

## 10. 扩展约定

- 新增节点：实现 `AgentState → AgentState` 的纯函数工厂（`make_xxx(llm, parser)`），在 `graph.py` 注册并声明路由；若该节点会发起 LLM 调用，先评估能否改为条件触发
- 替换 LLM / 数据库做测试：`build_agent(llm=FakeLLM(...), parser=DBParser(...), on_token=...)` 依赖注入，无需 mock 全局
- 持久化会话记忆：`build_agent(checkpointer=SqliteSaver(...))` + 固定 thread_id，REPL 重启后历史仍在（当前 CLI 用 MemorySaver 内存级）
- 切换 LLM 供应商：只改 `.env`（`LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_THINKING`），代码不动；非 GLM 模型不会收到 `thinking` 参数
- 换用官方 SDK：`LLM_BACKEND=langchain` 即切回 `langchain_openai.ChatOpenAI`（默认 native 客户端更快，二者接口契约一致：`invoke` / `stream` + `.content`）
- 切换数据库：改 `config.py` 的 `DATABASE_URL`，DBParser 自动适配（支持 sqlite / mysql；超时目前仅 sqlite 实现）
