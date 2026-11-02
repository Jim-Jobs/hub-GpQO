# SQL Agent 测试报告

| 项目 | 内容 |
|---|---|
| 项目名称 | LangGraph SQL Agent（Chinook 数据库自然语言查询） |
| 报告日期 | 2026-11-02（性能优化版 v4） |
| 测试框架 | pytest 9.0.3 |
| 执行环境 | Windows / Python 3.12.13（conda py312） |
| 测试命令 | `pytest -v --durations=10` |
| **测试结果** | ✅ **77 / 77 全部通过（0 失败 / 0 跳过），总耗时 10.5s** |

---

## 1. 结果总览

| 测试模块 | 用例数 | 通过 | 失败 | 是否依赖真实 LLM |
|---|---:|---:|---:|---|
| `test_db_parser.py`（DBParser 单元测试） | 11 | 11 | 0 | 否 |
| `test_safety.py`（SQL 安全校验） | 24 | 24 | 0 | 否 |
| `test_log.py`（日志模块） | 4 | 4 | 0 | 否 |
| `test_llm_config.py`（LLM 配置与客户端） | 15 | 15 | 0 | 否 |
| `test_graph.py`（Agent 图流程） | 20 | 20 | 0 | 否（FakeLLM） |
| `test_integration.py`（真实 LLM 集成） | 3 | 3 | 0 | **是**（GLM glm-4.5-air） |
| **合计** | **77** | **77** | **0** | — |

**离线可跑率**：74/77（96%）——不配置任何 API key 即可完成全流程回归验证。

---

## 2. 本轮（v4）新增用例

### 2.1 LLM 配置与客户端（`test_llm_config.py`，15 项 ✅）

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

### 2.2 图流程新增（`test_graph.py` 20 项中新增 5 项）

| 用例 | 验证点 | 结果 |
|---|---|:---:|
| `test_on_revise_approve_path` | 评审通过则不触发改写，**3 次 LLM 调用**（旧拓扑 4 次） | ✅ |
| `test_on_revise_revise_path` | 不通过才改写，改写提示含评审意见 | ✅ |
| `test_on_revise_loop_limit` | revise 恒失败时受 `MAX_OPTIMIZE_ROUNDS` 约束，不死循环 | ✅ |
| `test_streaming_answer` | `on_token` 逐块回调与最终 answer 一致 | ✅ |
| `test_streaming_fallback_without_stream` | LLM 无 stream 能力时自动回退一次性返回 | ✅ |

---

## 3. 保留用例概览（v3 已覆盖，本轮回归通过）

| 维度 | 关键用例 |
|---|---|
| 安全（24 项） | 9 类写操作全拦截（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA/ATTACH/VACUUM）、多语句注入、**字符串字面量不误杀**、数据库层只读、行数截断、执行超时 |
| 图流转（基础 10 项） | 成功路径 2 次调用、纠错回路闭环（错误回传）、重试耗尽兜底、写 SQL 拦截后自动恢复、表匹配与外键扩充、state 契约、多轮记忆、会话隔离 |
| 评审回路（旧拓扑 5 项） | 7 节点拓扑、approve 直通、revise 一轮、循环上限、改写失败回退原 SQL（`@pytest.mark.optimize_always`） |
| 数据层（10 项） | 11 张表解析、外键、字段元数据、样例、schema 缓存、行数量级、执行超时 |
| 日志（4 项） | 文件写入、幂等初始化、命名空间、级别继承 |

---

## 4. 性能专项

### 4.1 优化前后对比（同题库 3 题 / 真实 GLM 调用）

| 指标 | 优化前 | 优化后 | 提升 |
|---|---:|---:|---|
| 单问平均耗时 | 56.32s | **3.36s** | **16.8×** |
| 单问 LLM 调用次数 | 4 | **3** | −25% |
| 冷启动（导入 `sql_agent.graph`） | 5.02s | **3.03s** | 1.7× |
| 全量测试耗时 | 226s（仅集成 3 项） | **10.5s（77 项）** | 20×+ |

逐节点耗时（`python tools/bench_perf.py`）：

| 节点 | 优化前 | 优化后 |
|---|---:|---:|
| `write_query` | 9.10s | 1.59s |
| `optimize_query` | 17.22s | 0s（on_revise 跳过） |
| `review_query` | 13.46s | 0.67s |
| `generate_answer` | 16.44s | 0.96s |
| **合计** | **56.32s** | **3.23s** |

### 4.2 优化手段与验证

| 手段 | 验证方式 | 结论 |
|---|---|---|
| 关闭思维链（`thinking=disabled`） | 同一 prompt 对比 glm-4.5-air：7.0s → 1.7s，产出 SQL 完全一致 | 质量无损，延迟降 4× |
| 评审驱动改写（`on_revise`） | `test_on_revise_approve_path` 断言 3 次调用；revise 路径仍可纠错 | happy path −1 次调用 |
| 流式回答（`on_token`） | `test_streaming_answer` / `test_streaming_fallback_without_stream` | 首 token ~0.6s，无能力时自动回退 |
| 原生 httpx 客户端 | 冷启动 5.02s → 3.03s；角色映射与 SSE 各 5 项测试 | 省 ~2s 且连接复用 |
| 上下文瘦身（样例 3→2 行、pandas 出热路径） | 导入 `sql_agent.graph` 后 `pandas` / `langchain_openai` 均不在 `sys.modules` | 冷启动 −≈1.3s |

### 4.3 质量回归（优化后真实 GLM 端到端）

| 问题 | 结果 | 判定 |
|---|---|:---:|
| 数据库里一共有多少位客户？ | 59 位 | ✅ |
| 歌曲数量最多的前3个音乐流派？ | Rock 1297 / Latin 579 / Metal 374 | ✅ |
| 哪个国家的客户消费总额最高？ | USA 523.06 / Canada 303.96 / France 195.1 | ✅ |
| 销量最高的前3位艺术家（按订单金额）？ | Iron Maiden 138.6 / U2 105.93 / Metallica 90.09（四表 JOIN 一次通过） | ✅ |
| 多轮："那前5名呢？" | 正确理解为"流派歌曲数 Top5" | ✅ |

> 说明：优化前后答案逐条比对一致，说明性能优化未牺牲语义正确性。

---

## 5. 结论

| 维度 | 结论 |
|---|---|
| 功能 | 自然语言 → SQL → 执行 → 回答全链路可用，含评审回路与纠错回路 ✅ |
| 安全 | 只读白名单 + 只读连接 + 行数/超时限制，写操作零风险 ✅ |
| 健壮性 | 重试上限、改写回退、流式降级、JSON 解析降级、LLM 重试与异常包装 ✅ |
| 性能 | 单问 56.3s → 3.4s（16.8×），冷启动 5.0s → 3.0s，测试 76 项 10.5s ✅ |
| 可维护性 | 节点工厂 + 依赖注入 + 配置全走 .env，新增能力不动核心图 ✅ |

**验收标准「SQL Agent 可以正常使用」：✅ 达成**，且在性能上做了系统性优化并有基准脚本可复现。
