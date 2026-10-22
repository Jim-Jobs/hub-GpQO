# chinook-chatbi 技术设计文档

> 版本：v0.1（技术设计）｜日期：2026-10-22
>
> **文档边界**：本文档承接《chinook-chatbi 项目设计文档》v0.1（总体设计）与《chinook-chatbi 功能设计文档》v0.1（功能设计），回答"**怎么实现**"：总体技术结构与目录、契约（REST / SSE / progress / chart spec）字段级定稿、SQLite DDL、七步链路实现、安全防线细化、Prompt 设计、超时与并发参数、实现步骤。功能行为以功能设计文档为准（其 E1 断开语义已随本文档同步修订）。
>
> 术语沿用 `CONTEXT.md`；功能设计 §9 移交的 8 项事项在本文档全部落定（对应章节见 §12 决策记录）。

---

## 1. 总体技术结构

### 1.1 三端目录树

```
作业二/
├── CONTEXT.md                    # 术语表
├── chinook-chatbi 项目设计文档.md
├── chinook-chatbi 功能设计文档.md
├── chinook-chatbi 技术设计文档.md   # 本文
├── data/
│   └── chinook.db                # 复制自 作业一/backend/data（MD5 同课件版，见 §6.5），只读使用
├── mcp_server/                   # 进程③：无状态 MCP Server（端口 8001）
│   ├── app.py                    # MCPServer 实例、注册 analyze/get_schema、导出 streamable_http_app
│   ├── tools.py                  # AnalysisResult / SchemaSummary 类型定义
│   ├── pipeline.py               # 七步编排 + 校验-改写循环 + steps_trace 组装 + progress 上报
│   ├── db_parser.py              # 启动解析 chinook.db，进程内缓存（参考课件 DBParser）
│   ├── validator.py              # sqlglot AST 校验
│   ├── safety.py                 # mode=ro 连接 + set_authorizer 白名单 + LIMIT 200 包裹
│   ├── executor.py               # 只读执行，pandas 封装行数据
│   ├── prompts.py                # 选表 / SQL 生成 / 汇总 prompt 模板
│   └── llm.py                    # openai 封装：纯 JSON 自解析 + 重试 1 次
├── server/                       # 进程②：业务 Server（FastAPI，端口 8000）
│   ├── app.py                    # FastAPI 实例、路由注册、启动初始化
│   ├── routes/
│   │   ├── chat.py               # POST /api/chat（SSE）
│   │   ├── sessions.py           # GET /api/sessions、GET /api/sessions/{id}/messages
│   │   └── stream.py             # GET /api/sessions/{id}/stream（SSE 重订阅）
│   ├── orchestrator.py           # 一轮问答编排：建会话/落库/改写/调 MCP/事件广播/标题
│   ├── broadcaster.py            # 每轮事件缓冲 + 订阅者广播（chat 与 stream 共享）
│   ├── mcp_client.py             # MCP 客户端：每轮建连 + call_tool(progress_callback)
│   ├── llm.py                    # openai 封装（与 mcp_server/llm.py 同逻辑，进程独立各自实现）
│   ├── prompts.py                # 问题改写 / 标题生成 prompt
│   ├── db.py                     # aiosqlite 访问层 + DDL 初始化 + 重启兜底
│   └── data/
│       └── chatbi.db             # sessions / messages 双表
└── frontend/                     # 进程①：Vue3 + Vite（dev 5173）
    ├── src/
    │   ├── App.vue
    │   ├── main.ts
    │   ├── stores/
    │   │   ├── sessions.ts       # pinia：会话列表、当前会话
    │   │   └── chat.ts           # pinia：消息流、SSE 订阅管理、输入锁定
    │   ├── components/
    │   │   ├── SessionList.vue       # 左栏：新建按钮 + 会话列表
    │   │   ├── ChatFlow.vue          # 主区对话流（含空态切换）
    │   │   ├── UserBubble.vue        # 用户消息
    │   │   ├── AssistantBubble.vue   # 助手消息（组合以下子件）
    │   │   ├── RewriteHint.vue       # "理解为：…"小字
    │   │   ├── StepBar.vue           # 七步步骤条（点亮/打勾/标红/折叠）
    │   │   ├── AnswerText.vue        # markdown 渲染 + 打字机
    │   │   ├── ChartCard.vue         # chart spec → ECharts
    │   │   ├── FailedBubble.vue      # 失败气泡（可展开 SQL 尝试）
    │   │   └── InputBox.vue          # 底部输入（500 字限制、轮中禁用）
    │   ├── sse.ts                 # fetch 流式 SSE 解析器（EventSource 不支持 POST）
    │   ├── chart.ts               # chart spec → ECharts option 映射（bar/line/pie）
    │   └── types.ts               # 契约类型定义（与本文档 §3 一一对应）
    └── package.json
```

### 1.2 进程与端口

| 进程 | 启动命令 | 默认端口 | 端口来源 |
|---|---|---|---|
| MCP Server | `uvicorn mcp_server.app:app --port 8001` | 8001 | `.env` 可配 `MCP_PORT` |
| 业务 Server | `uvicorn server.app:app --port 8000` | 8000 | `.env` 可配 `SERVER_PORT` |
| 前端 | `cd frontend && npm run dev` | 5173 | Vite 默认 |

- 业务 Server 通过 `.env` 的 `MCP_URL`（默认 `http://127.0.0.1:8001/mcp`）定位 MCP Server，远程迁移零代码改动（总体设计 §8）；
- 端口常被占用的机器上改 `.env` 即可，不动代码。

### 1.3 环境与依赖

conda `py312`。已装：mcp 2.1.1 / openai 2.50.0 / sse-starlette 3.4.10 / fastapi 0.140.13 / uvicorn 0.40.0 / pandas 3.0.3。**需补装：`sqlglot`（校验步）、`aiosqlite`（业务 Server 异步访问）**。LLM 凭据沿用项目根 `.env`（`API_KEY` / `BASE_URL` / `BASE_MODEL=qwen3.8-max`）。

前端：vue 3 / vite / element-plus / vue-echarts / pinia / **无 vue-router**（单页）。

---

## 2. mcp 2.1.1 适配要点（事实核查结论）

以下均已在 py312 环境实测验证：

| 事项 | 结论 |
|---|---|
| 服务端类 | `from mcp.server.mcpserver import MCPServer`。`mcp.server.fastmcp` 已删除（墓碑模块，import 即报错指路迁移指南）——功能设计 §9-6 断言正确 |
| HTTP 挂载 | `MCPServer.streamable_http_app(*, streamable_http_path='/mcp', stateless_http=False, ...) -> Starlette`，返回 ASGI app 供 uvicorn 直接跑；本项目用 **`stateless_http=True`**（无会话状态，贴合总体决策 #7） |
| 工具内发 progress | 工具函数加 `ctx: Context` 类型注解参数即被注入（`Context` 自 `mcp.server.mcpserver` 导入）；`await ctx.report_progress(progress: float, total: float | None, message: str | None)`，**message 可携带任意字符串** |
| 客户端 transport | `from mcp.client.streamable_http import streamable_http_client`（注意是带下划线的新名；1.x 的 `streamablehttp_client` 已不存在）；`async with ... as (read, write)` **解包 2 元组**（无第三元素） |
| 客户端收 progress | `ClientSession.call_tool(name, arguments, read_timeout_seconds, progress_callback)`；回调签名 `async def cb(progress: float, total: float | None, message: str | None)`，三参、必须 async |
| openai SDK | `response_format` 参数存在（json_object / json_schema），但生效取决于模型服务端；本项目已决策走 prompt 约定 + 自解析（决策 #50），不使用 |

---

## 3. 契约定稿

### 3.1 REST 接口（前端 ↔ 业务 Server）

| 接口 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/api/chat` | POST | `{session_id?: string, message: string}`（message 非空且 ≤500 字，前端已拦） | `text/event-stream`（事件见 §3.2）；`session_id` 缺省 = 首轮，服务端建会话 |
| `/api/sessions` | GET | — | `{sessions: [{id, title, updated_at}]}`，按 `updated_at` 倒序 |
| `/api/sessions/{id}/messages` | GET | — | `{messages: [Message]}`，按 id 升序 |
| `/api/sessions/{id}/stream` | GET | — | `text/event-stream`：重订阅进行中轮次（§3.3）；该会话无 running 轮时立即结束流 |

> 总体设计 §5 中的 `POST /api/sessions` 已删除——功能设计 F1 定"新建不产生服务端请求、首条消息触发建会话"（决策 #48）。

**Message 结构**（`/messages` 返回项，与 SSE 事件字段同源）：

```jsonc
{
  "id": 42,                       // message_id，done 事件同款
  "role": "user" | "assistant",
  "content": "...",               // 用户原文 / 助手结论 / 失败原因
  "question_rw": "...",           // 改写后自包含问题（user 行，未改写则与 content 相同）
  "sql_text": "...",              // 最终执行 SQL（assistant 行，可空）
  "chart_spec": {...} | null,     // chart spec（§3.4），可空
  "status": "running" | "done" | "failed",   // user 行恒 done
  "steps_trace": [StepEvent] | null,         // 七步轨迹（§3.2 step 事件数组，含全部尝试）
  "error_message": "..." | null,  // failed 时的可读错误
  "created_at": "2026-10-22T12:00:00"
}
```

### 3.2 SSE 事件（六事件，chat 与 stream 两接口共用）

使用 SSE 原生 `event:` 字段区分类型；`data:` 为单行 JSON。一轮正常流：`session`(首轮) → `step`×N → `answer` → `chart`(可省) → `done`；失败流：`… → error`（此后无更多事件，流关闭）。

| 事件 | data payload | 说明 |
|---|---|---|
| `session` | `{session_id: string}` | **仅首轮**第一个事件；后续轮前端已持 id，不再发 |
| `step` | `{step: 1-7, name: string, status: "running"\|"done"\|"failed", attempt?: number, summary: string, sql?: string}` | 七步逐个推进；`attempt` 为 SQL 改写第 N 次尝试（仅第 5 步重试时出现）；`sql` 在第 3 步 done 时携带全文，其后每次改写尝试的新 SQL 亦随事件携带 |
| `answer` | `{text: string}` | 结论**全文一次性**下发；打字机由前端播放（功能设计 F4） |
| `chart` | `{spec: ChartSpec}` | chart spec 原文（§3.4）；单值/无图时不发 |
| `error` | `{message: string, attempts: [{sql: string, error: string}]}` | 可读错误 + 全部已尝试 SQL 与校验错误（供失败气泡展开） |
| `done` | `{message_id: number, title?: string}` | 轮完成；`title` 仅首轮携带（正式标题，生成失败不携带、保持占位） |

**step 事件与 steps_trace 的关系**：steps_trace = 该轮全部 step 事件按序存档（`/messages` 回放、历史回看用），两者结构完全同构。

### 3.3 progress 通知结构与映射（MCP → 业务 Server → 前端）

```
mcp_server pipeline 每步边界:
    await ctx.report_progress(
        progress = step_no / 7,                       # 百分比语义（0.14–1.0）
        total    = 1,
        message  = json.dumps(step_event)             # 与 §3.2 step 事件完全同构
    )

server mcp_client.progress_callback(progress, total, message):
    step_event = json.loads(message)                  # 零转换
    → broadcaster 广播 → SSE step 事件透传前端
    （同时由 orchestrator 追加进该轮 steps_trace）
```

- `progress.message` 与 SSE step payload **同构**，业务 Server 不做任何字段改写；
- SQL 全文随 message 走 JSON-RPC notification，无大小限制风险；
- 分析的最终结果（SQL/列/行/汇总/chart spec/steps_trace 全量）仍以 `call_tool` 返回值为准，progress 仅供实时展示。

### 3.4 chart spec（定稿）

```jsonc
{
  "type": "bar" | "line" | "pie" | null,   // null = 不画图（单值答案等）
  "title": "2013年销售额前5国家（按年份）",
  "x": { "field": "year", "label": "年份" },          // pie 时为 {field,label} 指扇区名字段
  "y": [                                              // 数组：多元素 = 多 series
    { "field": "total", "label": "销售额", "agg": "SUM" }   // agg: SUM|AVG|COUNT|MAX|MIN|空
  ],                                                  // pie 时为 [{field,label}] 指数值字段
  "rows": [                                           // 对象数组（LLM 产出最稳）
    { "year": "2013", "country": "USA", "total": 523.06 }
  ]
}
```

- `rows` ≤200 行（与执行截断一致），对象键 = SQL 结果列名/别名；
- 前端 `chart.ts` 负责 spec → ECharts option：`x.field` 为类目轴，`y[]` 每元素一条 series（bar/line）；pie 取 `x.field` 为 name、`y[0].field` 为 value。

### 3.5 MCP 工具返回（AnalysisResult / SchemaSummary）

**`analyze(question: str) -> AnalysisResult`**（`call_tool` 的 content，业务 Server 反序列化）：

```jsonc
{
  "ok": true,                       // false = 整轮失败（校验超限/LLM 超限等）
  "sql": "SELECT ...",              // 最终 SQL（失败时为最后一次尝试）
  "columns": ["country", "total"],  // 结果列名
  "rows": [[...], ...],             // 行数据（≤200，数组形态省流量）
  "row_count": 42,
  "truncated": false,               // 结果超 200 行被截断标记（截断提示依据）
  "summary": "…",                   // 文字结论（markdown）
  "chart_spec": {...} | null,
  "steps_trace": [StepEvent],       // 七步全轨迹（含每次 SQL 尝试与校验错误）
  "error": null                     // ok=false 时：{message, attempts:[{sql,error}]}
}
```

**`get_schema() -> SchemaSummary`**：`{tables: [{name, columns: [...], row_count}], foreign_keys: [{from, to}]}`，诊断/演示用（功能设计默认项 #37）。

---

## 4. 存储设计（业务 Server 侧）

### 4.1 DDL（定稿）

```sql
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,      -- UUID4（决策 #44）
  title       TEXT,                  -- 占位标题（前 12 字）→ 正式标题（LLM ≤12 字）
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP   -- 每轮 UPDATE，列表倒序依据
);

CREATE TABLE IF NOT EXISTS messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT REFERENCES sessions(id),
  role          TEXT,                -- user / assistant
  content       TEXT,                -- 用户原文 / 助手结论 / 失败原因
  question_rw   TEXT,                -- 改写后自包含问题（user 行）
  sql_text      TEXT,                -- 最终 SQL（assistant 行）
  chart_spec    TEXT,                -- JSON 字符串，可空
  status        TEXT,                -- assistant 行: running / done / failed（user 行恒 done）
  steps_trace   TEXT,                -- JSON：step 事件数组（含全部 SQL 尝试）
  error_message TEXT,                -- failed 时的可读错误
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
```

sessions 不加标题来源列：占位/正式标题行为等价（生成失败保持占位），无需区分（决策 #52）。

### 4.2 落库时机与轮状态机（逐步落库，决策 #42）

一轮问答的写库序列：

```
首轮：INSERT sessions(id=uuid4, title=question[:12])
INSERT messages(role=user, content=原文, question_rw=改写后, status=done)
INSERT messages(role=assistant, content='', status=running)      ← 轮开始即插入
UPDATE sessions SET updated_at=now
……分析进行中（状态只在内存 broadcaster，不写库）……
结束（成功）：UPDATE messages SET content=结论, sql_text, chart_spec,
              steps_trace, status='done' WHERE id=?
结束（失败）：UPDATE messages SET error_message, sql_text(最后尝试),
              steps_trace, status='failed' WHERE id=?
首轮成功追加：UPDATE sessions SET title=LLM标题（失败不动）
UPDATE sessions SET updated_at=now
```

**重启兜底**：业务 Server 启动时 `UPDATE messages SET status='failed', error_message='服务重启，分析中断' WHERE status='running'`——遗留 running 一律转 failed（断开≠失败语义下，running 只可能因进程退出而遗留，决策 #47）。

aiosqlite 单连接串行写，事务即开即关；单用户演示无写竞争。

---

## 5. 七步链路实现（mcp_server/pipeline.py）

编排伪码：

```python
async def analyze(question: str, ctx: Context) -> AnalysisResult:
    trace = []
    # ① 解析：db_parser 启动已缓存，瞬时 done
    await emit(ctx, trace, 1, "解析", done="库结构已缓存")
    # ② 选表
    tables = await select_tables(question)          # llm + prompts.SELECT_TABLES
    await emit(ctx, trace, 2, "选表", done=f"选中 {len(tables)} 表：{'、'.join(tables)}")
    # ③④⑤ 生成-校验-改写循环：生成 1 次 + 改写最多 3 次（总尝试 ≤4）
    sql = await gen_sql(question, tables)           # llm + prompts.GEN_SQL
    await emit(ctx, trace, 3, "生成", done="已生成 SQL", sql=sql)
    for attempt in range(4):                        # 0..3
        verdict = validator.check(sql)              # sqlglot AST 校验
        await emit(ctx, trace, 4, "校验", done="通过" if verdict.ok else f"失败：{verdict.error}")
        if verdict.ok:
            break
        if attempt == 3:
            return AnalysisResult(ok=False, sql=sql, steps_trace=trace,
                                  error=f"SQL 校验连续不通过（已尝试 {attempt+1} 次）", ...)
        sql = await rewrite_sql(sql, verdict.error) # llm（prompt 内联错误信息）
        await emit(ctx, trace, 5, "改写", attempt=attempt+1, done=f"第 {attempt+1} 次尝试", sql=sql)
    # ⑥ 执行
    cols, rows, truncated = executor.run_readonly(sql)   # safety + pandas
    await emit(ctx, trace, 6, "执行",
               done=f"返回 {len(rows)} 行" + ("（已截断）" if truncated else ""))
    # ⑦ 汇总
    summary, chart = await summarize(question, sql, cols, rows, truncated)
    await emit(ctx, trace, 7, "汇总", done="完成")
    return AnalysisResult(ok=True, sql=sql, columns=cols, rows=rows,
                          truncated=truncated, summary=summary,
                          chart_spec=chart, steps_trace=trace)
```

各步实现要点：

| 步 | 模块 | 要点 |
|---|---|---|
| ① 解析 | `db_parser.py` | 启动时 SQLAlchemy 反射 chinook.db：11 张业务表的字段/类型/注释、11 条外键、每表 3 行样本；产出两档文本——**全库摘要**（表名+一行说明+外键关系，供选表）与**字段级 schema**（供生成，仅注入选中表）。进程内全局缓存 |
| ② 选表 | `pipeline.py` + `prompts.SELECT_TABLES` | few-shot 1 例；输出 `{tables: [...], reason}`；reason 进步骤摘要不做他用 |
| ③ 生成 | `prompts.GEN_SQL` | 注入选中表字段级 schema + 样本行；输出 `{sql}`；约束写死：单条 SELECT、SQLite 方言、只用给定表、列名加别名便于图表 |
| ④ 校验 | `validator.py` | 见 §6.1；输出 `{ok, error, position?}`，错误信息供改写回喂与失败气泡展示 |
| ⑤ 改写 | `prompts.REWRITE_SQL`（inline） | prompt 内联上次 SQL 与校验错误；循环上限见伪码——**生成 1 次 + 改写 3 次**，与总体设计 §3.3"失败重试上限 3 次"一致 |
| ⑥ 执行 | `safety.py` + `executor.py` | 见 §6.2/6.3；pandas DataFrame 承接，产出 columns/rows(数组)/truncated |
| ⑦ 汇总 | `prompts.SUMMARIZE` | 行数据以 markdown 表格注入（≤200 行全量）；输出 `{summary, chart}`；截断轮在 prompt 中告知"结果已截断至 200 行"，结论尾部固定附截断提示（功能设计 E3） |

**LLM 输出统一容错**（`llm.py`，两进程同逻辑）：prompt 约定仅输出 JSON → 剥 ```` ```json ```` 围栏 → `json.loads` → 失败携原始输出与解析错误重试 1 次 → 仍失败抛 `LLMOutputError`（选表/生成/改写/汇总步抛出即该轮走失败路径；问题改写步由业务 Server 降级用原文，功能决策 #21）。

---

## 6. 安全设计（三层防线细化）

### 6.1 第一层：sqlglot AST 白名单（validator.py）

1. `sqlglot.parse(sql, read="sqlite")` —— 解析失败、或产出 **多于一个语句** → 拒绝（"仅允许单条语句"）；
2. 唯一语句必须为 `sqlglot.exp.Select` —— INSERT/UPDATE/DELETE/PRAGMA/ATTACH/DDL 一律结构性拒绝（"仅允许单条 SELECT"）；
3. 遍历 AST 收集全部函数名，对照**函数白名单**：聚合（COUNT/SUM/AVG/MIN/MAX/GROUP_CONCAT）、数值（ABS/ROUND）、字符串（UPPER/LOWER/LENGTH/SUBSTR/REPLACE/TRIM/INSTR）、空值（COALESCE/NULLIF/IFNULL）、日期（DATE/STRFTIME/JULIANDAY/DATETIME）、CAST 表达式；白名单外函数拒绝（拦 `writefile`/`readfile`/`load_extension` 等）；
4. 拒绝带 position 与人话原因（如"第 2 个语句不被允许：PRAGMA"），供改写回喂与用户阅读。

### 6.2 第二层：内核级拦截（safety.py）

**两类连接严格分离**：db_parser 的解析连接（启动时跑自家代码，`mode=ro` 即可、不设 authorizer——SQLAlchemy 反射内部用 PRAGMA，设了会被自己拦死）≠ executor 的执行连接（专跑 LLM 产物 SQL，`mode=ro` + `set_authorizer` 双防）。

执行连接策略（实测定稿，清单见 §6.4）：

- **放行（按数值比较，勿按常量名——action 码与 error 码共享数值，按名映射会串号）**：`20 SQLITE_READ`（表/列读取）、`21 SQLITE_SELECT`、`31 SQLITE_FUNCTION`（函数调用——此层不重复维护函数白名单，但 arg2 函数名命中 `{load_extension, readfile, writefile}` 黑名单时 DENY 兜底，防特殊构建）、`33 SQLITE_RECURSIVE`（递归 CTE 属合法读，如 employees 层级查询）；
- **拒绝**（返回 `SQLITE_DENY`）：一切未明确放行的动作码——**默认拒绝**（default-deny），实测确认 INSERT/UPDATE/DELETE/DDL/ALTER/PRAGMA（含 pragma 虚表）/ATTACH/DETACH/BEGIN/SAVEPOINT/ANALYZE/REINDEX/VACUUM 全部在 prepare 阶段被拦；
- **必须 DENY、禁用 IGNORE**：实测 `SQLITE_IGNORE` 对 DELETE 是**真实执行删除**（静默），写防护只能用 DENY；
- 回调按 5 参数签名 `(action, arg1, arg2, db_name, trigger)` 编写：实测签名错/回调内异常/返回 None 一律按 DENY 处理——**fail-closed，写错回调 = 锁死而非失守**。

### 6.3 第三层：只读连接 + 强限量（safety.py / executor.py）

- `mode=ro` 打开：即使前两层全被绕过，SQLite 文件级只读（实测写语句报 `attempt to write a readonly database`，文件零变化）；连接设 `isolation_level=None`（autocommit）——默认隔离级别下失败语句会遗留打开事务，导致后续报错信息混乱（实测陷阱）；
- **LIMIT 包裹**：不文本注入（易破坏语义），改为子查询包裹——`SELECT * FROM ({sql}) AS _sub LIMIT 201`；取前 200 行，若结果 201 行则 `truncated=True`（截断信息流经执行步摘要、汇总 prompt、结论尾部提示三处，功能设计 E3）；
- 执行超时：`conn.execute` 前设 interrupt timer（简单实现：执行线程 + 超时回调 `conn.interrupt()`），上限 10s，防失控查询挂死轮次。

### 6.4 authorizer 动作码实测清单（py312 + SQLite 3.53.2）

白名单 `{20 READ, 21 SELECT, 31 FUNCTION, 33 RECURSIVE}` + 默认 DENY 的实测验证结果：

| 语句类型 | 实际下发的动作（数值=名，arg） | 白名单下 |
|---|---|---|
| SELECT 列 | 21 SELECT + 每列 20 READ(表,列)；rowid 为 READ arg2='' | ✅ 通过 |
| SELECT 函数/聚合 | 额外 31 FUNCTION(None, 函数名) | ✅ 通过 |
| 递归 CTE | 33 RECURSIVE | ✅ 通过 |
| EXPLAIN / EQP | 与底层 SELECT 同动作流 | ✅ 通过（只读，无害） |
| INSERT / UPDATE / DELETE | 18 INSERT / 23 UPDATE / 9 DELETE | ❌ prepare 期拦截 |
| CREATE / DROP / ALTER / ANALYZE / REINDEX | 2/11/10/17/16/26/8/7/27 系 + sqlite_master 的 18/9/2 | ❌ 拦截 |
| PRAGMA（含 pragma 虚表） | 19 PRAGMA(pragma名, 值) | ❌ 拦截（表结构走 sqlite_master 查询或解析连接） |
| ATTACH / DETACH | 24 / 25 | ❌ 拦截 |
| BEGIN / SAVEPOINT | 22 TRANSACTION / 32 SAVEPOINT | ❌ 拦截（autocommit 纯 SELECT 无需事务） |
| VACUUM | — | ❌ 拦截（报错消息为 "authorization denied"，特殊但无害） |

关键实测结论（实现红线）：

1. **动作码按数值比较**：模块 168 个 `SQLITE_*` 常量中 error 码与 action 码共享数值（如 21 既 = `SQLITE_SELECT` 也 = `SQLITE_MISUSE`），按名字映射会串号；
2. **IGNORE 禁用于写防护**：实测 `SQLITE_IGNORE` 下 DELETE 真实执行删除（静默）、INSERT/UPDATE 静默丢弃——写动作必须 `SQLITE_DENY`；
3. **fail-closed**：回调以 5 参数调用；签名错误、回调内抛异常、返回 None/字符串一律按 DENY——写错回调导致全库锁死而非失守；
4. **FUNCTION 黑名单兜底**：`readfile`/`writefile` 在 Python sqlite3 构建中本就不存在（fileio.c 是 CLI 扩展）、`load_extension` 默认禁用，但 arg2 黑名单兜底可防特殊构建；
5. **叠加验证**：`mode=ro` + authorizer 可叠加，两层独立生效（写语句被 authorizer 先拦，报 "not authorized" 而非 readonly）。

### 6.5 chinook.db 来源（实测定位）

全 badou2026 仅两份库文件，MD5 相同（`f2dd976ac500525eb862a008ba21609c`，884,736 字节，同一文件）：课件 `week11/code/04_SQL-Code-Agent-Demo/chinook.db` 与 **`邓帅/week11/作业一/backend/data/chinook.db`**（M1 时复制后者到 `作业二/data/chinook.db`）。均为 11 张业务表版本（另含 sqlite_sequence/sqlite_stat1 两张内部表；表名小写 snake_case，无视图无触发器，10 个 IFK_* 外键索引）：albums 347 / artists 275 / customers 59 / employees 8 / genres 25 / invoice_items 2240 / invoices 412 / media_types 5 / playlist_track 8715 / playlists 18 / tracks 3503。

---

## 7. 业务 Server 实现要点（server/）

### 7.1 一轮编排（orchestrator.py）

```
POST /api/chat(session_id?, message) 处理序列：
1. session_id 缺省 → INSERT sessions（UUID4、占位标题）→ 发 session 事件（首轮）
2. 取最近 5 轮历史 → llm 改写（问题改写 prompt；失败重试 1 次→降级用原文）
3. INSERT user 行（原文 + question_rw）；INSERT assistant running 行
4. mcp_client.call("analyze", {question: 改写后问题},
                   read_timeout_seconds=180, progress_callback=broadcaster.on_progress)
   → progress 实时转 SSE step；返回 AnalysisResult
5. ok=True  → UPDATE assistant 行(done)；发 answer → chart(如有) → [首轮: LLM 标题, ≤10s] → done(带 title)
   ok=False → UPDATE assistant 行(failed)；发 error(含 attempts)
6. UPDATE sessions.updated_at
```

- 会话内输入锁定由前端执行（决策 #29），服务端不拦跨会话并发（切离会话后台照跑）；
- 首轮标题生成在 done 事件**之前**（决策 #53）：多 1–2s 延迟换取 done 携带新标题、列表零额外请求即时更新。

### 7.2 事件广播与重订阅（broadcaster.py + routes/stream.py）

```python
class TurnBroadcaster:
    events: list[str]          # 该轮已发 SSE 事件的有序缓冲（含 event: 行 + data: 行）
    subscribers: set[asyncio.Queue]
    # publish(evt): events.append → 逐 queue.put
    # subscribe() -> asyncio.Queue: 先按序重放 events，再实时收后续
```

- `POST /api/chat` 与 `GET /api/sessions/{id}/stream` 写读同一 broadcaster；
- **重订阅流程**（决策 #46/#47）：前端切回会话 → 拉 `/messages` → 尾部有 `status=running` 的 assistant 行 → 显示"分析进行中"占位并 `GET /stream` 订阅 → 服务端重放缓冲事件 + 续推后续 → 收 `done`/`error` 关流；若订阅时轮已恰好结束，服务端立即发该轮终态事件后关流（竞态由库状态兜底：前端收流后再拉一次 `/messages` 校平）；
- 轮结束后 broadcaster 缓冲即弃（终态以库为准），内存占用有界；
- `stream` 接口对无 running 轮的会话：立即结束流（前端不会处于此状态，防御性行为）。

### 7.3 MCP 客户端（mcp_client.py，决策 #54）

每轮新建连接，不做进程级复用：

```python
async with streamable_http_client(MCP_URL) as (read, write):      # 2 元组解包（§2）
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("analyze", {"question": q},
                                         read_timeout_seconds=180,
                                         progress_callback=on_progress)
```

本地握手毫秒级，与 MCP 无状态定位对齐，免连接池/重连/并发锁。

### 7.4 超时与并发参数表（决策 #49）

| 项 | 值 | 落点 |
|---|---|---|
| LLM 单次调用 | timeout 30s；网络/解析失败重试 1 次 | 两进程 `llm.py` |
| MCP analyze 整体 | 180s | `call_tool(read_timeout_seconds=180)`（覆盖最长路径 ~7 次 LLM 调用+执行） |
| 标题生成 | 10s，失败降级占位 | orchestrator |
| SQL 执行 | 10s interrupt | safety |
| SSE 心跳 | ping 15s | sse-starlette `EventSourceResponse(ping=15)`，chat/stream 两接口 |
| worker | 单进程单 worker ×2 服务 | uvicorn |
| 防挂死 | MCP 超时/异常 → 该轮 failed 落库 + error 事件 | orchestrator try/except 全包 |

---

## 8. 前端实现要点（frontend/）

- **SSE 消费**（`sse.ts`）：`fetch('/api/chat', {method:'POST', body})` + ReadableStream 手写解析器（按 `\n\n` 分帧、`event:`/`data:` 拆行）——EventSource 不支持 POST；重订阅 `stream` 接口可用原生 EventSource（GET），但为统一解析逻辑仍走同一解析器；
- **pinia stores**（决策 #56）：`sessions`（列表/当前/新建视图态）、`chat`（当前会话消息流、订阅生命周期、输入锁定、打字机状态）；
- **步骤条**（StepBar）：固定 7 步常驻；step 事件按 `step` 序号点亮、`status` 着色、`attempt` 显示"第 N 次尝试"、`sql` 展示全文；全部完成后折叠为"七步分析完成"，展开显示各步摘要与 SQL 尝试（数据源即 steps_trace 结构）；
- **打字机**（AnswerText）：`answer` 全文按 ~20 字/帧 `setInterval` 切片播放，播放完解锁输入；回看/重订阅重放时整段直显（默认项 #35）；
- **图表**（ChartCard + `chart.ts`）：chart spec → ECharts option 映射三转换器（bar/line/pie，多 y 元素=多 series）；`type:null` 不渲染卡片；
- **失败态**（FailedBubble）：error 事件的 message + attempts 可展开；"重试"= 以原问题追加新一轮（功能设计 5.2）；
- **空态**（EmptyState）：无会话时 3–4 个示例问题点击填入（功能设计 F8）；
- 输入约束：空不发、>500 字禁发+提示（功能设计 E4）。

---

## 9. Prompt 设计（五篇全文）

统一规则：全中文；系统消息定角色与输出契约（仅输出 JSON、无围栏）；用户消息注入上下文；解析走 §5 的统一容错。`{...}` 为运行时注入槽。

### 9.1 问题改写（server/prompts.py，编排层）

```text
你是 ChatBI 的问题改写器。用户正在就 chinook 音乐销售数据做多轮问答。
当前问题可能包含指代（"那再按年份拆一下呢"），请结合最近对话历史，
把它改写为**不依赖任何上下文即可理解的完整问题**。

规则：
1. 不回答问题，只改写；不添加原文没有的分析维度。
2. 若当前问题已是自包含（通常首轮如此），原样返回。
3. 仅输出 JSON：{"rewritten": "改写后的问题"}

【最近对话】
{最近 5 轮：user 原文 / assistant 结论摘要（各截 200 字）}
【当前问题】{message}
```

### 9.2 选表（mcp_server/prompts.py）

```text
你是 SQL 分析的选表器。给定 chinook 数据库的全库表关系摘要和一个自包含问题，
选出回答该问题所需的全部表（含 join 中转表，如 invoice_items）。

规则：只从摘要中列出的表里选；宁多勿漏（缺表无法生成正确 SQL）。
仅输出 JSON：{"tables": ["表名", ...], "reason": "一句话理由"}

【全库表关系摘要】
{db_parser 全库摘要：11 张表名+一行说明+外键关系}

【示例】
问题：2013 年销售额最高的 5 个国家是哪些？
输出：{"tables": ["invoices", "customers"], "reason": "销售额在 invoices（BillingCountry），按国家聚合"}

【问题】{question}
```

### 9.3 SQL 生成（mcp_server/prompts.py）

```text
你是 SQLite SQL 编写器。给定选定表的字段级结构（含样本行）和问题，
编写**一条** SELECT 语句回答它。

规则：
1. 只用给定表的字段；SQLite 方言；不写多语句、不写 PRAGMA、只读查询。
2. 聚合结果列用 AS 起有语义的英文别名（如 SUM(Total) AS total），
   便于下游图表直接引用。
3. 数值列输出保留两位小数（ROUND）。
4. 仅输出 JSON：{"sql": "SELECT ..."}

【表结构】
{db_parser 字段级 schema：选中表的建表信息+每表 3 行样本}

【示例】
问题：每种音乐流派各有多少首曲目？
输出：{"sql": "SELECT g.Name AS genre, COUNT(t.TrackId) AS track_count FROM genres g JOIN tracks t ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY track_count DESC"}

【问题】{question}
```

### 9.4 SQL 改写（inline 于 pipeline ⑤）

```text
你之前生成的 SQL 未通过校验。根据错误信息修正它。
规则不变：单条 SELECT、SQLite 方言、只用给定表；仅输出 JSON：{"sql": "..."}

【上次的 SQL】{sql}
【校验错误】{error}
```

### 9.5 结果汇总（mcp_server/prompts.py）

```text
你是数据分析师。给定问题、SQL 与查询结果，产出文字结论与图表描述。

结论规则：
1. 用中文直接回答问题，先给结论再给要点（markdown：列表/加粗/简单表格）。
2. 数值给单位（美元金额、首/人次）；不编造结果之外的事实。
{若截断}3. 结论最后一行固定附："**结果超过 200 行，已展示前 200 行**"。

图表规则（按数据形态选型）：
- 单值答案（1 行 1 列）→ chart.type 为 null，不配图；
- 类别对比 → "bar"；时间趋势 → "line"；构成占比（类别 ≤8 且含占比语义）→ "pie"；
- x/y 的 field 必须是结果列的别名；rows 原样复制结果数据（对象数组）；
- 仅输出 JSON：{"summary": "...", "chart": {type,title,x,y,rows} | null}

【问题】{question}
【SQL】{sql}
【查询结果（列：{columns}）】
{rows 的 markdown 表格，≤200 行}
```

### 9.6 标题生成（server/prompts.py，编排层）

```text
为下面的问答生成一个不超过 12 个字的会话短标题（如"2013 各国销售额"）。
概括话题、不含标点；仅输出 JSON：{"title": "..."}

【问题】{首轮原文}
【结论】{summary 前 200 字}
```

---

## 10. 实现步骤（里程碑）

| 里程碑 | 内容 | 完成判据 |
|---|---|---|
| **M1 MCP Server** | 复制 chinook.db（§6.5）；补装 sqlglot；safety + validator + db_parser（各带单测：authorizer 动作码按 §6.4 清单、SQL 拒绝样例、11 表解析）；pipeline + prompts + llm；app.py 挂 `streamable_http_app(stateless_http=True)` | 单测绿；冒烟脚本直连调用 `analyze("2013 年销售额最高的 5 个国家")` 返回 ok=true、SQL 正确、chart 合理；`get_schema` 可用 |
| **M2 业务 Server** | aiosqlite 补装 + DDL + 重启兜底；sessions/messages 接口；orchestrator（改写/落库/MCP 客户端/SSE 六事件）；broadcaster + stream 重订阅；标题生成 | curl 走通：首轮建会话收六事件、二轮指代消解改写可见、失败轮 error+落库、断开重连 stream 续看 |
| **M3 前端** | 脚手架（Vite+EP+pinia+vue-echarts）；会话栏与空态；对话流+步骤条+SSE 解析；图表/打字机/失败气泡；输入约束与锁定 | 三进程联跑：F1–F8 全行为按功能设计逐条过 |
| **M4 联调与验收** | 多轮指代用例、截断用例（>200 行问题）、只读拒绝用例（"删除所有客户"）、断开重订阅用例、回看用例、刷新页面用例 | 功能设计 F1–F8 × 异常路径全数通过；演示脚本就绪 |

---

## 11. 附录：功能设计 E1 修订说明

功能设计文档 §5.1 原文「SSE 流中途断开（视为该轮失败，E1）」**修订**为：

> SSE 流中途断开：分析在服务端后台继续执行并照常落库；用户切回该会话或刷新页面后，通过重订阅（`/api/sessions/{id}/stream`）续看进行中轮次，或直接回看已完成结果。仅当业务 Server 重启时，遗留的进行中轮次才被标记为失败（"服务重启，分析中断"）。

修订原因：技术设计选定 SSE 重订阅作为切回刷新机制（决策 #46），若断开即失败则重订阅无流可续、机制不成立（决策 #47）；修订后语义自洽——状态以库为准，连接断开不改变轮状态。

---

## 12. 决策记录（续总体 #1–16、功能 #17–34，本文 #42 起）

| # | 决策项 | 结论 | 关键理由 |
|---|---|---|---|
| 42 | 落库时机 | 逐步落库：轮开始插 running 行、结束 UPDATE | 失败照常落库、回放一致；切回判定只依赖库 |
| 43 | chart spec | 对象数组 rows + y 多 series + type:null 不画图 | LLM 产出最稳、多 series 天然支持 |
| 44 | 会话标识 | UUID4 + 独立 session 事件（仅首轮） | 事件语义单一 |
| 45 | 文档体例 | 沿用前两份 + 实现步骤章节（M1–M4） | 与功能设计 §17 分工衔接 |
| 46 | 切回刷新机制 | SSE 重订阅（/stream 接口，缓冲重放+实时续推） | 体验最佳；状态在库、断开不失败作前提 |
| 47 | SSE 断开语义 | 断开≠失败：后台跑完落库、重连续看；仅重启标 failed | 使 #46 成立；功能设计 E1 同步修订 |
| 48 | REST 定稿 | 删 POST /api/sessions，三接口+stream | 以功能设计 F1 为准（新建零请求） |
| 49 | 超时并发 | LLM 30s/重试1、MCP 180s、标题 10s、心跳 15s、单进程 | 覆盖最长路径 ~7 次 LLM 调用 |
| 50 | 结构化输出 | prompt 约定纯 JSON + 自解析 + 重试 1 次 | 不依赖模型端 response_format 支持度 |
| 51 | SSE 事件 | 六事件定稿；answer 全文一次性，打字机前端播放 | 零协议增量分片，观感由前端实现 |
| 52 | DDL | messages +status/steps_trace/error_message；sessions 不加列 | 行为等价无需区分标题来源 |
| 53 | 标题时机 | done 事件前生成、done 携带 | 列表即时更新、零额外接口 |
| 54 | MCP 客户端 | 每轮新建连接（streamable_http_client 2 元组） | 无状态对齐、免连接治理 |
| 55 | mcp 模块结构 | 九文件扁平树；MCPServer + stateless_http=True | 事实核查后的新 API；各司一职 |
| 56 | 前端选型 | pinia + 单页无路由 | 状态跨组件共享；实现最简 |
| 57 | 端口 | 维持 8000/8001，.env 可配 | 与总体 §8 一致；占端冲突改配置即可 |
| 58 | LIMIT 实现 | 子查询包裹 LIMIT 201 取 200，非文本注入 | 不破坏原 SQL 语义，截断判定可靠 |

**§9 移交事项落定对照**：#1 REST/SSE→§3.1/3.2；#2 progress 映射→§3.3；#3 chart spec→§3.4；#4 DDL→§4；#5 Prompt→§9；#6 mcp 2.x 适配→§2；#7 刷新机制→§7.2；#8 超时并发→§7.4。
