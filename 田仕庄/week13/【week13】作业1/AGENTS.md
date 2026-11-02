# AGENTS.md — Agent 开发规范

本文档定义本项目中 SQL Agent 的设计规范、节点职责、状态契约与扩展约定。

## 1. Agent 定位

自然语言 → SQL → 执行 → 自然语言答案 的**只读查询 Agent**，运行在 Chinook（SQLite）数据库上，交付形态为命令行（CLI 单次提问 + REPL 交互）。

**环境约定**：Python 解释器统一使用 conda `py312` 环境；所有可变配置（LLM / 数据库 / 日志 / 重试）一律走 `.env`，代码中不硬编码。

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

节点均为**工厂函数**（`make_xxx(llm, parser)`），依赖注入便于测试替换为 Fake 实现。提示词通过 `history_block()` 注入最近 3 轮问答，支持"那前10名呢？"这类指代。

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
| 回归方式 | 改动后用 `python tools/bench_perf.py` 对比逐节点耗时，并在 TEST_REPORT 记录前后数据 |

## 7. 测试规范（`tests/`，共 77 项）

| 文件 | 数量 | 说明 |
|---|---|---|
| `test_db_parser.py` | 11 | 元数据解析、外键、样例、只读连接、schema 缓存、行数统计、执行超时、Markdown 渲染（离线） |
| `test_safety.py` | 24 | 白名单校验矩阵、写操作拦截、字符串字面量不误杀、行数截断（离线） |
| `test_log.py` | 4 | 文件写入、幂等初始化、命名空间、级别继承（离线） |
| `test_llm_config.py` | 15 | 思维链参数注入/不注入、快慢模型回退、角色映射、SSE 解析、重试与异常（离线） |
| `test_graph.py` | 20 | FakeLLM：基础链路 10 + 评审回路 5 + on_revise 3 + 流式回答 2（离线） |
| `test_integration.py` | 3 | 真实 LLM 端到端，评审回路开启（`@pytest.mark.integration`） |

- 测试隔离策略（conftest）：`query_review` 标记开启评审；`optimize_always` 标记强制旧拓扑（脚本化响应顺序敏感）；其余单测默认关闭评审；integration 跟随 .env
- 离线测试（74 项）不依赖真实 LLM：`pytest -m "not integration"`
- 集成测试（3 项）需有效 key：`pytest -m integration`
- marker 在 `pytest.ini` 注册；提交前 `pytest -v` 必须 77 项全绿

## 8. 日志规范（`sql_agent/log.py`）

- 入口处调用一次 `setup_logging()`；各模块统一 `get_logger(__name__)` 取 logger（挂在 `sql_agent.*` 命名空间）
- 双通道：控制台（stderr）+ 滚动文件 `logs/sql_agent.log`（1MB/份，保留 3 份）
- 级别约定：`INFO` 节点关键事件（表命中/SQL 生成/执行成败/路由）；`DEBUG` 明细（结果预览/图构建）；`WARNING` 安全拦截与执行错误
- **严禁**将 API key、完整用户敏感数据写入日志

## 9. 扩展约定

- 新增节点：实现 `AgentState → AgentState` 的纯函数工厂（`make_xxx(llm, parser)`），在 `graph.py` 注册并声明路由；若该节点会发起 LLM 调用，先评估能否改为条件触发
- 替换 LLM / 数据库做测试：`build_agent(llm=FakeLLM(...), parser=DBParser(...), on_token=...)` 依赖注入，无需 mock 全局
- 持久化会话记忆：`build_agent(checkpointer=SqliteSaver(...))` + 固定 thread_id，REPL 重启后历史仍在（当前 CLI 用 MemorySaver 内存级）
- 切换 LLM 供应商：只改 `.env`（`LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_THINKING`），代码不动；非 GLM 模型不会收到 `thinking` 参数
- 换用官方 SDK：`LLM_BACKEND=langchain` 即切回 `langchain_openai.ChatOpenAI`（默认 native 客户端更快，二者接口契约一致：`invoke` / `stream` + `.content`）
- 切换数据库：改 `config.py` 的 `DATABASE_URL`，DBParser 自动适配（支持 sqlite / mysql；超时目前仅 sqlite 实现）
