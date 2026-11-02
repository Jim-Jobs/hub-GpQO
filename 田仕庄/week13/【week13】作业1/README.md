# SQL Agent（LangGraph 实现）

基于 **LangGraph** 的 Chinook 数据库自然语言查询 Agent：用户用自然语言提问，Agent 自动生成 SQL、安全执行、失败自动纠错重试，最终返回自然语言答案。

## 功能特性

- 🗄️ **数据库解析**：自动解析表结构、行数量级、外键关系、样例数据（改造自课程 notebook 的 `DBParser`，单表 schema 带缓存，热路径不依赖 pandas）
- 🤖 **LangGraph 状态图**：`get_schema → write_query → review_query →(revise) optimize_query → review_query → execute_query → generate_answer`
- 🔍 **评审回路（Reflection 模式）**：评审节点按 checklist 把关语义正确性（表选择/聚合口径/JOIN/限行），不通过才触发改写（最多 2 轮）；改写引入错误时自动回退原 SQL
- 🔄 **失败重试回路**：执行失败自动纠错重写（最多 3 次），错误信息回传 LLM
- 🔒 **SQL 安全防护**：只读白名单校验（仅允许 SELECT/WITH，字符串字面量不误杀）+ SQLite 只读连接 + 行数截断 + 语句级执行超时
- 💬 **多轮会话记忆**：checkpointer + thread_id，支持"那前5名呢？"这类指代提问；CLI 逐步展示节点流转并逐节点计时
- ⚡ **性能优化**：关闭思维链 + 评审驱动改写（3 次 LLM 调用）+ 流式回答 + 原生 httpx 客户端，单问 **56.3s → 3.4s（约 17 倍）**
- 📝 **日志模块**：console + 滚动文件双通道，记录节点流转、评审结论、重试路由全过程
- ✅ **自动化测试**：pytest 77 项（74 离线 mock LLM + 3 真实 LLM 集成）

## 架构设计

```
                                                    ┌── revise（≤2轮，带评审意见）──┐
                                                    ▼                              │
用户提问 ─▶ [get_schema] ─▶ [write_query] ─▶ [review_query] ─▶ [optimize_query] ───┘
 (START)    匹配相关表结构     生成SQL并保存原稿    语义评审把关      按意见专业化改写
                                                    │ approve
                                                    ▼
                                    [execute_query] ─┬─成功─▶ [generate_answer] ─▶ 回答
                                     安全校验+执行    │        （流式输出）
                                    （失败可回退原SQL）└─失败─▶ [rewrite_query] ─┐
                                              ▲                错误反馈重试(≤3次) │
                                              └───────────────────────────────────┘
```

- 默认 **评审驱动改写**（`OPTIMIZE_STRATEGY=on_revise`）：SQL 通过评审就不改写，happy path 只花 3 次 LLM 调用
- 需要"无条件先改写再评审"的旧拓扑时设 `OPTIMIZE_STRATEGY=always`
- 评审回路可通过 `.env` 的 `ENABLE_QUERY_REVIEW=false` 一键关闭（退回 5 节点基础链路）

## 项目结构

```
├── README.md              # 本文件
├── AGENTS.md              # Agent 开发规范文档
├── requirements.txt       # 依赖清单
├── pytest.ini             # pytest 配置（integration 标记）
├── .env.example           # 环境变量模板（复制为 .env 使用）
├── chinook.db             # SQLite 示例数据库
├── sql_agent/             # 核心包
│   ├── config.py          # 配置管理（环境变量加载）
│   ├── db_parser.py       # DBParser：数据库解析与安全执行（pandas 延迟导入）
│   ├── llm.py             # LLM 工厂：思维链开关 + 快慢双模型 + 后端选择
│   ├── llm_client.py      # 轻量 OpenAI 兼容客户端（httpx + SSE 流式）
│   ├── log.py             # 日志模块（console + 滚动文件双通道）
│   ├── state.py           # LangGraph State 定义
│   ├── nodes.py           # 7 个图节点实现（含流式回答）
│   └── graph.py           # StateGraph 组装（条件边/重试/评审路由）
├── main.py                # CLI 入口（单次提问 / 多轮 REPL，流式 + 逐节点计时）
├── tools/bench_perf.py    # 性能基准脚本（逐节点耗时 + LLM 调用次数）
├── logs/                  # 运行时生成的日志目录（sql_agent.log）
└── tests/                 # pytest 测试模块（77 项）
    ├── test_db_parser.py  # DBParser 单元测试（11 项，含超时/缓存/渲染）
    ├── test_safety.py     # SQL 安全校验测试（24 项，含字面量不误杀）
    ├── test_log.py        # 日志模块测试（4 项）
    ├── test_llm_config.py # LLM 配置与客户端测试（15 项，思维链/角色映射/SSE/重试）
    ├── test_graph.py      # Agent 图流程测试（20 项，含评审回路/改写策略/流式/多轮记忆）
    └── test_integration.py# 真实 LLM 集成测试（3 项，需 .env）
```

## 快速开始

### 1. 环境准备

```bash
conda activate py312          # 项目约定使用 conda py312 解释器
pip install -r requirements.txt
```

### 2. 配置 LLM

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（智谱 GLM key，格式：xxxxxxxx.yyyyyyyy）
```

默认使用智谱 GLM（`https://open.bigmodel.cn/api/paas/v4`，模型 `glm-4.5-air`——免费、多表 JOIN 能力好，且支持关闭思维链）。如需切换其他 OpenAI 兼容服务（DeepSeek、OpenAI 等），修改 `.env` 中的 `LLM_BASE_URL` 与 `LLM_MODEL` 即可；非 GLM 模型不会注入 `thinking` 参数。

### 3. 运行

```bash
# 单次提问
python main.py "销量最高的前5位艺术家是谁？"

# 交互模式（REPL，支持多轮指代：连续问"那前10名呢？"）
python main.py
# REPL 命令：/new 开新会话（清空对话记忆） | exit 退出
```

### 4. 运行测试

```bash
# 离线全量测试（74 项，mock LLM，无需真实 key）
pytest -v -m "not integration"

# 真实 LLM 集成测试（3 项，需配置 .env，评审回路开启）
pytest -v -m integration

# 全部（77 项）
pytest -v
```

### 5. 性能基准

```bash
python tools/bench_perf.py                 # 内置 3 道题（易/中/难）
python tools/bench_perf.py "你的问题"       # 自定义
```

输出逐节点耗时、LLM 调用次数与总耗时，便于优化前后对比。

## 配置速查（`.env`）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_MODEL` | `glm-4.5-air` | 主模型（生成/修正 SQL） |
| `LLM_FAST_MODEL` | 空 | 辅助模型（改写/评审/回答），空则同主模型 |
| `LLM_THINKING` | `auto` | 思维链：`auto`（推理模型自动关）/ `enabled` / `disabled` |
| `LLM_BACKEND` | `native` | `native`（httpx 直连，快）/ `langchain` |
| `LLM_MAX_RETRIES` | `1` | LLM 请求重试次数 |
| `ENABLE_QUERY_REVIEW` | `true` | 评审回路总开关（false 退回 5 节点链路） |
| `OPTIMIZE_STRATEGY` | `on_revise` | `on_revise`（省一次调用）/ `always`（旧拓扑） |
| `MAX_OPTIMIZE_ROUNDS` | `2` | revise 改写轮次上限 |
| `MAX_RETRIES` | `3` | SQL 执行失败重写上限 |
| `STREAM_ANSWER` | `true` | 回答流式输出 |
| `SCHEMA_SAMPLE_ROWS` | `2` | 上下文中每表样例行数（0 关闭） |
| `QUERY_TIMEOUT` | `10` | 单条 SQL 执行超时（秒） |
| `MAX_RESULT_ROWS` | `50` | 结果行数上限 |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `logs/sql_agent.log` | 日志级别与文件 |

## 性能

### 优化前后对比（同一题库、同一数据库、真实 GLM 调用）

| 指标 | 优化前 | 优化后 | 提升 |
|---|---:|---:|---|
| 单问平均耗时 | 56.32s | **3.36s** | **16.8×** |
| 单问 LLM 调用次数 | 4 次 | **3 次** | -25% |
| 单问输出 token 与首字延迟 | 整段返回 | 流式（首 token ~0.6s） | 感知大幅改善 |
| 进程冷启动（导入） | 5.0s | **3.0s** | 1.7× |
| 集成测试 3 项总耗时 | 226s | **27.5s** | 8.2× |

逐节点实测（`python tools/bench_perf.py`）：

```
优化前：write_query 9.10s + optimize_query 17.22s + review_query 13.46s + generate_answer 16.44s = 56.32s
优化后：write_query 1.59s + review_query 0.67s + generate_answer 0.96s                       = 3.23s
```

### 四项关键手段

| 手段 | 说明 | 收益 |
|---|---|---|
| **关闭思维链** | GLM-4.5 系列默认深度思考，单次 10~20s。`thinking={"type":"disabled"}` 后质量不变、延迟骤降（`LLM_THINKING` 可随时切回） | 最大单项收益 |
| **评审驱动改写** | 原拓扑无条件"改写→评审"，改写结果常与初稿相同。改为"评审→不通过才改写"，happy path 少一次调用（`OPTIMIZE_STRATEGY=on_revise`） | -25% 调用 |
| **流式回答 + 原生客户端** | 回答边生成边输出（首 token ~0.6s）；用 httpx 直连替代 `langchain_openai`（后者仅导入就要 4.2s），并复用连接 | 感知延迟 + 冷启动 -2s |
| **上下文瘦身** | 样例行数 3→2（`SCHEMA_SAMPLE_ROWS`）、热路径移除 pandas 依赖、单表 schema 片段缓存 | 冷启动 -1.3s，prefill 更短 |

> 为什么不去动数据库层：Chinook 最大表 3503 行，SQL 执行耗时 0.00~0.01s，占比 <1%。瓶颈 100% 在 LLM 往返，优化必须集中在"减少调用次数 + 降低单次延迟"。

## 日志

运行时会同时输出到**控制台**和 `logs/sql_agent.log`（滚动文件，1MB/份，保留 3 份），记录每个节点的流转：

- `INFO`：表命中、SQL 生成、执行成败、重试路由、回答完成
- `DEBUG`：结果预览、图构建细节（`.env` 中设 `LOG_LEVEL=DEBUG` 开启）
- `WARNING`：SQL 安全校验拦截、执行错误、重试耗尽

配置项（`.env`）：`LOG_LEVEL`、`LOG_FILE`。

## 示例

```
你: 每种音乐流派各有多少首歌曲？
  ⏳ get_schema: 分析问题、匹配相关表结构  (0.0s)
  ⏳ write_query: 生成 SQL  (1.5s)
  ⏳ review_query: 评审 SQL 质量  (0.7s)
  ⏳ execute_query: 安全校验并执行  (0.0s)

  [SQL]
  SELECT g.Name AS genre, COUNT(t.TrackId) AS track_count
  FROM genres g JOIN tracks t ON g.GenreId = t.GenreId
  GROUP BY g.Name ORDER BY track_count DESC;

  [结果]
  | genre | track_count |
  |:--|:--:|
  | Rock | 1297 |
  | Latin | 579 |
  …

  ⏳ generate_answer: 组织自然语言回答
🤖 各音乐流派歌曲数量如下：Rock（摇滚）最多，有 1297 首；其次是 Latin（拉丁）579 首……

  ⏱  总耗时 3.43s  |  get_schema 0.0s · write_query 1.5s · review_query 0.7s · execute_query 0.0s · generate_answer 1.2s
```

## 技术要点

| 项目 | 说明 |
|---|---|
| 图框架 | `langgraph.graph.StateGraph`，条件边控制评审/重试路由 |
| LLM | 默认 `sql_agent.llm_client.ChatClient`（httpx + SSE，OpenAI 兼容）；`LLM_BACKEND=langchain` 可切 `langchain_openai.ChatOpenAI` |
| ORM | SQLAlchemy 2.x（inspect 反射元数据） |
| 安全 | SELECT/WITH 白名单 + SQLite `mode=ro` 只读连接 + 结果行数上限 + 语句级超时 |
| 性能 | 思维链开关 + 快慢双模型 + 评审驱动改写 + 流式回答 + 连接复用 |
| 日志 | `logging` + RotatingFileHandler（1MB×3 滚动），`sql_agent.*` 命名空间 |
| 测试 | pytest + mock LLM（FakeLLM 脚本化响应），integration / query_review / optimize_always 标记隔离 |

## 开发规范

参与本项目的开发约定（节点职责、状态契约、安全红线、日志与测试规范）详见 [AGENTS.md](AGENTS.md)。
