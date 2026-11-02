# SQL Agent 测试报告

| 项目 | 内容 |
|---|---|
| 项目名称 | LangGraph SQL Agent（Chinook 数据库自然语言查询） |
| 报告日期 | 2026-11-02（性能复测与稳定性修复版 v11） |
| 测试框架 | pytest 9.0.3 + FastAPI TestClient |
| 执行环境 | Windows / Python 3.12.13（conda py312）/ Node 22 |
| 测试命令 | `pytest -v`（离线：`pytest -m "not integration"`） |
| **测试结果** | ✅ **101 / 101 全绿（16.52s）**：97 项离线 + 4 项真实 LLM 集成。上一轮遗留的 GLM key 过期问题已解决（用户换新 key），集成 4 项本轮复核通过 |

---

## 1. 结果总览

| 测试模块 | 用例数 | 通过 | 失败 | 是否依赖真实 LLM |
|---|---:|---:|---:|---|
| `test_db_parser.py`（DBParser 单元测试） | 11 | 11 | 0 | 否 |
| `test_safety.py`（SQL 安全校验） | 24 | 24 | 0 | 否 |
| `test_log.py`（日志模块） | 4 | 4 | 0 | 否 |
| `test_llm_config.py`（LLM 配置与客户端） | 15 | 15 | 0 | 否 |
| `test_graph.py`（Agent 图流程） | 20 | 20 | 0 | 否（FakeLLM） |
| `test_web.py`（Web API / SSE） | 24 | 24 | 0 | 23 否（FakeLLM）/ 1 是 |
| `test_integration.py`（真实 LLM 集成） | 3 | 3 | 0 | **是**（GLM glm-4.5-air） |
| **合计** | **101** | **101** | **0** | — |

**离线可跑率**：97/101（96%）——不配置任何 API key 即可完成全流程回归验证（`pytest -m "not integration"`，~4s）。

---

## 2. 用例明细（按模块）

### 2.1 SQL 安全校验（`test_safety.py`，24 项 ✅）

9 类写操作全拦截（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA/ATTACH/VACUUM）、多语句注入、**字符串字面量不误杀**（`WHERE x='DELETE'` 合法）、数据库层只读、行数截断、执行超时。

### 2.2 图流程与评审回路（`test_graph.py`，20 项 ✅）

| 分组 | 覆盖 |
|---|---|
| 基础链路（10） | 成功路径 2 次调用、纠错回路闭环（错误回传）、重试耗尽兜底、写 SQL 拦截后自动恢复、表匹配与外键扩充、state 契约、多轮记忆、会话隔离 |
| 评审回路·旧拓扑（5，`@optimize_always`） | 7 节点拓扑、approve 直通、revise 一轮、循环上限、改写失败回退原 SQL |
| on_revise 策略（3） | approve 路径仅 **3 次 LLM 调用**、revise 注入评审意见、revise 恒失败受 `MAX_OPTIMIZE_ROUNDS` 约束不死循环 |
| 流式回答（2） | `on_token` 逐块回调与最终 answer 一致；LLM 无 stream 能力时自动回退一次性返回 |

### 2.3 数据层（`test_db_parser.py`，11 项 ✅）

11 张表解析、外键关系、字段元数据、样例数据、schema 缓存、行数量级、执行超时、Markdown 渲染。

### 2.4 日志（`test_log.py`，4 项 ✅）

文件写入、幂等初始化、命名空间、级别继承。

### 2.5 LLM 配置与客户端（`test_llm_config.py`，15 项 ✅）

| 用例 | 验证点 | 结果 |
|---|---|:---:|
| `test_thinking_auto_disables_for_reasoning_models` | auto 模式对 glm-4.5/4.6/5/z1 注入 `thinking=disabled`（核心提速手段） | ✅ |
| `test_thinking_not_injected_for_non_reasoning_models` | glm-4-flash / gpt-4o / deepseek 不注入，保证跨供应商可移植 | ✅ |
| `test_thinking_explicit_enabled` | 显式 `enabled` 可恢复深度思考 | ✅ |
| `test_thinking_disabled_ignores_unsupported_model` | 模型不支持时仍不注入（不会 400） | ✅ |
| `test_is_reasoning_model` | 推理模型前缀识别（含大小写） | ✅ |
| `test_get_llm_applies_thinking_and_retries` | 客户端带 thinking 与收紧后的重试次数 | ✅ |
| `test_get_llm_requires_api_key` | 缺 key 时抛明确错误，而非发请求才失败 | ✅ |
| `test_fast_llm_falls_back_to_main` | 辅助模型留空时回退主模型 | ✅ |
| `test_fast_llm_uses_configured_model` | 配置后辅助节点走更轻模型 | ✅ |
| `test_describe_llm` | 启动摘要含模型与思维链状态 | ✅ |
| `test_client_maps_langchain_roles` | `human→user / ai→assistant` 角色映射（否则服务端报"角色信息不正确"） | ✅ |
| `test_client_injects_extra_body` | thinking / max_tokens 平铺进请求体 | ✅ |
| `test_client_parse_sse_line` | SSE 增量解析：正常/空增量/`[DONE]`/非 data 行 | ✅ |
| `test_client_invoke_retries_then_succeeds` | 429 后重试成功 | ✅ |
| `test_client_invoke_raises_after_retries` | 重试耗尽抛 `LLMError` | ✅ |

### 2.6 Web API / SSE（`test_web.py`，24 项 ✅）

| 分组 | 覆盖 |
|---|---|
| 基础接口与事件（6） | 健康检查（运行配置）、新会话发号、入参校验（空/超长 422）、SSE 事件协议（node/sql/result/done）、多轮记忆（thread_id 复用注入历史）、失败路径（rewrite 重试 3 次 + 失败说明） |
| 性能优化（6） | 极速模式跳过评审回路（节点序列无 review）、评审回路开启时节点齐全、health 暴露 fast_mode、token 批处理（窗口合并 + 首块立即发）、token 拼接与最终回答一致、预热失败仅告警不阻塞 |
| 连接层（3） | CORS 跨域预检与实际请求（`access-control-allow-origin: *`）、SSE 心跳保活（含 `: ping` 且业务事件完整）、超时发出 error 事件并释放串行锁 |
| 锁可靠性（3） | `_LockGuard` 幂等释放且换锁后旧 guard 不误放、泄漏锁（未启动/超时）自动自愈、客户端读一半断开后下一次请求不返回 409 |
| 问答缓存（4） | 新会话重复提问命中回放且 LLM 调用数不变、同一会话追问因历史不同不串答案、`nocache` 与 `WEB_CACHE_TTL=0` 都能关闭、健康检查暴露缓存开关与条目数 |
| 静态托管（1） | dist/index.html 由 `server.py` 返回 200 |
| 集成（1） | 真实 LLM SSE 流式端到端（事件齐全、token 拼接与最终回答一致） |

---

## 3. 性能专项

### 3.1 Agent 侧（CLI）：单问 56.3s → 3.4s（16.8×）

同题库 3 题 / 真实 GLM：

| 指标 | 优化前 | 优化后 | 提升 |
|---|---:|---:|---|
| 单问平均耗时 | 56.32s | **3.36s** | **16.8×** |
| 单问 LLM 调用次数 | 4 | **3** | −25% |
| 冷启动（导入 `sql_agent.graph`） | 5.02s | **3.03s** | 1.7× |
| 全量测试耗时 | 226s（仅集成 3 项） | **10.5s（当时 77 项）** | 20×+ |

逐节点耗时（`python tools/bench_perf.py`）：`write_query` 9.10s→1.59s；`optimize_query` 17.22s→0（on_revise 跳过）；`review_query` 13.46s→0.67s；`generate_answer` 16.44s→0.96s。

手段与验证：关闭思维链（同一 prompt 7.0s→1.7s，产出一致）、评审驱动改写（approve 路径 −1 次调用）、流式回答（首 token ~0.6s）、原生 httpx 客户端（冷启动 −2s）、上下文瘦身（pandas / langchain_openai 不进热路径）。

### 3.2 Web 侧：首 token 3.66s → 1.85s（−49%）

`tools/bench_web.py`，同一题库 3 题 × 2 次采样，真实 GLM：

| 指标 | 优化前 | 优化后（严谨） | 优化后（**极速**，页面默认） | 提升 |
|---|---:|---:|---:|---|
| **首 token（TTFT）** | 3.66s | 3.42s | **1.85s** | **−49%** |
| 单问总耗时 | 4.45s | 4.56s | **2.83s** | −36% |
| token 事件数 | 69 | 26 | **22** | **−62%** |
| 首事件延迟 | 0.01s | 0.01s | 0.01s | — |

手段：极速/严谨双模式（编译期两条图）、token 批量下发（40ms 窗口 + 前端按帧上屏）、启动预热（`WEB_WARMUP`）、上下文瘦身（排名类追问走核心表）、等待实时计时。SSE 转发零额外 LLM 调用，Web 层开销 <5ms；前端产物 gzip 后 41.38 kB。

> 极速（默认）延迟最低，严谨保留评审把关——复杂/易错查询可切回严谨，质量优先。
> 复现：`python tools/bench_web.py --repeat 2`（严谨）/ `--fast`（极速）；LLM 侧有波动，采样 ≥2 次。脚本默认带 `nocache`，测真实 LLM 延迟；加 `--cache` 才允许命中问答缓存。

### 3.2.1 本轮复测（v11，3 题 × 2 次，真实 GLM，2026-11-02 晚）

| 指标 | 严谨模式 | 极速模式（页面默认） | 极速 + 缓存命中 |
|---|---:|---:|---:|
| **首 token（TTFT）** | 3.31s | **1.77s** | **0.00s** |
| 单问总耗时 | 4.11s | **2.56s** | **0.00~0.01s** |
| token 事件数 | 18 | 18 | 18（完整回放） |
| 首事件 | 0.01s | 0.01s | 0.00s |

- 与 v6 数据（严谨 3.42s / 极速 1.85s）基本持平，LLM 往返已接近下限，瓶颈确认为 `write_query`（~1.2s）+ `generate_answer`（首 token ~0.5s + 流式 ~1s）。
- 严谨模式复杂题（"销量最高的前5位艺术家"）6.26~7.12s，其中评审 → 改写 → 复审回路约占 3.7s（日志：`review 1.36 + optimize 1.65 + review 0.65`），属质量取舍；简单查询走默认极速即可。
- **缓存命中**：新会话重复提问（键含会话历史与模式）直接回放完整 SSE 事件，首 token / 总耗时均降到 ~0.01s，页面显示「⚡ 缓存命中 · 原耗时 Xs」；不占串行锁、不调用 LLM。多轮追问因历史不同不会误命中。
- 基准脚本默认 `nocache=True`，避免重复题目命中缓存导致真实延迟数据失真。

### 3.3 质量回归（优化后真实 GLM 端到端）

| 问题 | 结果 | 判定 |
|---|---|:---:|
| 数据库里一共有多少位客户？ | 59 位 | ✅ |
| 歌曲数量最多的前3个音乐流派？ | Rock 1297 / Latin 579 / Metal 374 | ✅ |
| 哪个国家的客户消费总额最高？ | USA 523.06 / Canada 303.96 / France 195.1 | ✅ |
| 销量最高的前3位艺术家（按订单金额）？ | Iron Maiden 138.6 / U2 105.93 / Metallica 90.09（四表 JOIN 一次通过） | ✅ |
| 多轮："那前5名呢？" | 正确理解为"流派歌曲数 Top5" | ✅ |

> 优化前后答案逐条比对一致——性能优化未牺牲语义正确性。

---

## 4. 端到端验证（真实服务 + 真实 GLM，2026-11-02）

| 场景 | 结果 | 数据 |
|---|:---:|---|
| 页面加载 | ✅ | `/` 200（738B）+ JS/CSS 资源 200（113KB/9KB） |
| 第一问（含评审 revise 回路） | ✅ | "销量最高的前5位艺术家" → Iron Maiden 140 / U2 107 / Metallica 91…，总 6.84s |
| 多轮指代 | ✅ | "那前10名呢？" → 自动 LIMIT 10，总 4.57s，happy path 3 次 LLM 调用 |
| 跨域 SSE（预览面板 / Vite dev 直连） | ✅ | 带 `Origin` 请求返回 CORS 头，事件流完整 |
| 长空闲不断流 | ✅ | 每 15s `: ping` 心跳；总超时按累计空闲判定 |
| 失败兜底 | ✅ | 重试耗尽 → done 事件给出友好失败说明 |
| 并发保护 | ✅ | 单请求串行锁，忙时 409；超时/断连正确释放锁 |
| **客户端中途断开**（v11） | ✅ | `curl … \| head` 截断 SSE 后，下一次查询返回 200（修复前永久 409）；worker `finally` 兜底释放 + 自愈换锁 |
| **问答缓存**（v11） | ✅ | 重复提问首 token/总耗时 0.00~0.01s，事件序列完整回放；`done` 带 `cached`/`origin_elapsed`；health 返回 `cache:{enabled,size}` |
| 日志 | ✅ | `sql_agent.web` 命名空间写入滚动文件：chat 入口/done 耗时/节点明细/缓存命中 |
| 未知路径 | ✅ | 404 |

**连接层专项（`Failed to fetch` 修复，v7）**：先用 curl 直连排除服务端问题（SSE 完整 200、无锁泄漏、并发 409 正确），定位为**缺 CORS + SSE 无心跳**两个连接层成因；修复后补前端可诊断化——网络错误翻译为中文排查步骤、`file://` 打开场景识别、加载即健康检查横幅（含"重新检测"按钮）、启动失败可操作提示。

---

## 5. 结论

| 维度 | 结论 |
|---|---|
| 功能 | 自然语言 → SQL → 执行 → 回答全链路可用，含评审回路与纠错回路；**Web 端对话（SSE 流式）可用** ✅ |
| 安全 | 只读白名单 + 只读连接 + 行数/超时限制，写操作零风险；Web 层请求校验 + 并发保护 ✅ |
| 健壮性 | 重试上限、改写回退、流式降级、JSON 解析降级、LLM 重试与异常包装、连接层可诊断、**串行锁断连自愈** ✅ |
| 性能 | 单问 56.3s → 3.4s（16.8×），冷启动 5.0s → 3.0s；Web 首 token 3.66s → **1.85s**（−49%）、token 事件 −62%；本轮复测极速 1.77s / 严谨 3.31s，缓存命中 ~0.01s；全量 101 项 16.52s ✅ |
| 可维护性 | 节点工厂 + 依赖注入（含 Web 层 `create_app(agent=...)`）+ 配置全走 .env + 测试基建单一来源（`tests/fakes.py`） ✅ |

**验收标准「页面功能可以正常使用」：✅ 达成**——Web 页面问答、流式回答、多轮指代、执行过程可视化、SQL/结果展示均验证通过。

---

## 6. 变更历史

| 轮次 | 变更 | 用例总数 |
|---|---|---:|
| v1–v3 | 基础链路 + 安全 + 日志 + LLM 配置测试建立 | 71 |
| v4（11-02） | 性能优化轮：思维链开关 / on_revise / 流式 / 原生客户端（+6） | 77 |
| v5（11-02） | 新增 Web 前端模块：FastAPI + SSE + Vue 3（test_web +8） | 85 |
| v6（11-02） | Web 性能优化：极速模式 / token 批处理 / 启动预热（+6） | 91 |
| v7（11-02） | 修复 `Failed to fetch`：CORS + SSE 心跳 + 前端可诊断化（+3） | 94 |
| v8（11-02） | `main.py` 统一入口（+4，后被 v9 回退） | 98 |
| v9（11-02） | 回退统一入口：`main.py` 恢复纯 CLI，删除 test_cli（−4）；保留参数防呆 | 94 |
| v10（11-02） | 结构优化：测试基建收敛到 `tests/fakes.py`（离线回归 16s→4s）、`NODE_LABELS` 单一来源 `sql_agent/labels.py`、文档数字修正（test_web 实为 17 项）、本报告重构；⚠️ 复跑时 GLM key 过期（401），集成 4 项待换 key 复核 | 94 |
| v11（11-02） | 性能复测与稳定性修复：①串行锁泄漏（客户端断开后永久 409）→ 幂等 guard 三重释放 + 自愈；②带会话历史感知的问答缓存（+ 配置/nocache/基准开关）；③前端 `done` 事件答案重复修复；重跑基准并同步全部文档；GLM key 已恢复，集成 4 项复核通过（+7） | **101** |
