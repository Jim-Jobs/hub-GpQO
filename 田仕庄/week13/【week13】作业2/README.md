# SQL Agent（LangGraph 实现）

基于 **LangGraph** 的 Chinook 数据库自然语言查询 Agent：用户用自然语言提问，Agent 自动生成 SQL、安全执行、失败自动纠错重试，最终返回自然语言答案。

> **怎么用**：网页版 `python server.py` 后打开 <http://127.0.0.1:8000>；命令行 `python main.py "你的问题"`；进命令行交互模式 `python main.py`（不带参数，不启动网页）。
> 安装与日常操作见 [快速开始](#快速开始)（第 0 节是"日常使用速查表"）。

## 功能特性

- 🗄️ **数据库解析**：自动解析表结构、行数量级、外键关系、样例数据（改造自课程 notebook 的 `DBParser`，单表 schema 带缓存，热路径不依赖 pandas）
- 🤖 **LangGraph 状态图**：`get_schema → write_query → review_query →(revise) optimize_query → review_query → execute_query → generate_answer`
- 🔍 **评审回路（Reflection 模式）**：评审节点按 checklist 把关语义正确性（表选择/聚合口径/JOIN/限行），不通过才触发改写（最多 2 轮）；改写引入错误时自动回退原 SQL
- 🔄 **失败重试回路**：执行失败自动纠错重写（最多 3 次），错误信息回传 LLM
- 🔒 **SQL 安全防护**：只读白名单校验（仅允许 SELECT/WITH，字符串字面量不误杀）+ SQLite 只读连接 + 行数截断 + 语句级执行超时
- 💬 **多轮会话记忆**：checkpointer + thread_id，支持"那前5名呢？"这类指代提问；CLI 逐步展示节点流转并逐节点计时
- 🌐 **Web 对话界面（Vue 3）**：浏览器输入自然语言即可查询；SSE 流式回答 + 执行过程时间线 + SQL/结果表格可视化 + 一键新会话，多轮指代与 CLI 一致
- ⚡ **双模式响应速度**：页面可切换 `⚡ 极速`（跳过评审，首字 ~1.9s）与 `🛡 严谨`（评审把关，更稳）；配合 token 批量下发 + 前端按帧渲染 + 启动预热，Web 首字延迟 **3.66s → 1.85s**
- ⚡ **性能优化**：关闭思维链 + 评审驱动改写（3 次 LLM 调用）+ 流式回答 + 原生 httpx 客户端，单问 **56.3s → 3.4s（约 17 倍）**
- 📝 **日志模块**：console + 滚动文件双通道，记录节点流转、评审结论、重试路由、Web 请求全过程
- ✅ **自动化测试**：pytest 101 项（97 离线 mock LLM + 4 真实 LLM 集成，含 Web SSE 端到端）

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

Web 形态（server.py + Vue 前端）：
浏览器(Vue 3) ──POST /api/chat──▶ FastAPI ──SSE──▶ {node / sql / result / token / done}
                  fetch 流式解析     └─▶ 复用同一个 LangGraph Agent（MemorySaver 多轮记忆）
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
│   ├── labels.py          # 节点进度文案（CLI 与 Web 共用的单一来源）
│   ├── log.py             # 日志模块（console + 滚动文件双通道）
│   ├── state.py           # LangGraph State 定义
│   ├── nodes.py           # 7 个图节点实现（含流式回答）
│   └── graph.py           # StateGraph 组装（条件边/重试/评审路由）
├── main.py                # CLI 入口（单次提问 / 多轮 REPL，流式 + 逐节点计时）
├── server.py              # Web 服务入口（FastAPI + SSE 流式接口，托管前端产物）
├── web/frontend/          # Vue 3 前端（Vite 构建）
│   ├── src/App.vue        # 对话主界面（消息流 / 输入区 / 欢迎页）
│   ├── src/components/MessageItem.vue  # 助手消息卡片（时间线/SQL/结果表格/流式回答）
│   ├── src/lib/api.js     # SSE 客户端（fetch 流式解析）+ 会话接口
│   └── dist/              # 构建产物（gzip 后约 41KB，由 server.py 托管）
├── tools/bench_perf.py    # 性能基准脚本（逐节点耗时 + LLM 调用次数）
├── tools/bench_web.py     # Web 端 SSE 基准（首 token / 各阶段耗时 / 事件数，支持 --fast / --cache）
├── logs/                  # 运行时生成的日志目录（sql_agent.log）
└── tests/                 # pytest 测试模块（101 项）
    ├── test_db_parser.py  # DBParser 单元测试（11 项，含超时/缓存/渲染）
    ├── test_safety.py     # SQL 安全校验测试（24 项，含字面量不误杀）
    ├── test_log.py        # 日志模块测试（4 项）
    ├── test_llm_config.py # LLM 配置与客户端测试（15 项，思维链/角色映射/SSE/重试）
    ├── test_graph.py      # Agent 图流程测试（20 项，含评审回路/改写策略/流式/多轮记忆）
    ├── test_web.py        # Web API 测试（24 项，SSE 协议/极速模式/批处理/CORS/心跳/锁自愈/问答缓存）
    ├── fakes.py           # 测试共用 LLM 替身（FakeLLM / FakeStreamLLM）
    └── test_integration.py# 真实 LLM 集成测试（3 项，需 .env）
```

## 快速开始

### 0. 日常使用速查（环境已配好后，只看这张表就够了）

| 想做的事 | 怎么做 |
|---|---|
| **打开网页问答（推荐）** | `conda activate py312` → `python server.py` → 浏览器访问 <http://127.0.0.1:8000> |
| 停止 Web 服务 | 在启动服务的终端按 `Ctrl + C` |
| 换端口启动 | `WEB_PORT=8080 python server.py`，或改 `.env` 里的 `WEB_PORT` |
| 命令行问一句 | `python main.py "销量最高的前5位艺术家是谁？"` |
| 命令行多轮聊天 | `python main.py`（输入 `/new` 开新会话，`exit` 退出） |
| 跑测试 | `pytest -m "not integration"`（97 项，无需 key） |
| 测性能 | `python tools/bench_perf.py`（CLI）/ `python tools/bench_web.py`（Web） |
| 改了前端代码 | `cd web/frontend && npm run build`，再刷新 http://127.0.0.1:8000 |

> 所有命令都在**项目根目录**下执行，且统一使用 conda `py312` 解释器。
>
> **两个入口别混**：`python server.py` 启动网页服务（FastAPI + 前端）；`python main.py` 是命令行入口（不带参数进 REPL，不会启动网页服务）。

### 1. 首次安装（只需做一次）

```bash
# 1) 激活环境并安装依赖
conda activate py312
pip install -r requirements.txt

# 2) 生成 .env 并填入 LLM key
cp .env.example .env          # Windows cmd 用：copy .env.example .env
#    编辑 .env，把 LLM_API_KEY 换成自己的智谱 GLM key（格式：xxxxxxxx.yyyyyyyy）

# 3) Web 前端：产物已预构建（web/frontend/dist），可直接使用
#    只有在需要改前端时才需要 Node 18+ 与 npm install（见第 4 节）
```

默认使用智谱 GLM（`https://open.bigmodel.cn/api/paas/v4`，模型 `glm-4.5-air`——免费、多表 JOIN 能力好，且支持关闭思维链）。如需切换其他 OpenAI 兼容服务（DeepSeek、OpenAI 等），修改 `.env` 中的 `LLM_BASE_URL` 与 `LLM_MODEL` 即可；非 GLM 模型不会注入 `thinking` 参数。

### 2. 日常使用 · Web 界面（推荐）

**① 启动服务**

```bash
conda activate py312
python server.py               # 启动网页服务（独立入口，与 CLI 无关）
# 换端口：WEB_PORT=8080 python server.py
```

终端出现 `sql_agent.web | 启动 Web 服务: http://127.0.0.1:8000` 即启动成功；该终端保留即可实时查看节点流转日志（同时也会写入 `logs/sql_agent.log`）。

> 若终端没有这行提示、而是出现 `SQL Agent（LangGraph + Chinook）` 和一个 `你:` 输入提示，说明启动的是命令行入口 `python main.py`（它不提供网页服务）。按 `exit` 退出，改用 `python server.py`。

**② 打开页面**：浏览器访问 <http://127.0.0.1:8000>。

**③ 开始提问**：在底部输入框用自然语言描述问题，`Enter` 发送（`Shift + Enter` 换行）。也可以直接点欢迎页的示例问题。

页面会实时展示 Agent 的完整工作过程：

| 页面元素 | 说明 |
|---|---|
| 执行过程时间线 | 依次显示"匹配表结构 → 生成 SQL → 评审 → 执行 → 组织回答"及每步耗时 |
| SQL 代码块 | 展示生成的 SQL，右上角可一键复制 |
| 查询结果表格 | 以表格形式展示执行结果（表头可吸顶滚动） |
| 流式回答 | 答案逐字输出，无需等待整段生成完 |
| ✨ 新会话 | 右上角按钮，清空对话记忆、开启新的话题 |
| ⚡ 极速 / 🛡 严谨 | 右上角模式切换（默认极速）：极速跳过评审回路、延迟更低；严谨保留 SQL 评审把关，复杂查询更稳 |
| 等待计时 | 等待期间显示"正在查询… 2.3s"实时计时与已完成的节点耗时 |
| ⚡ 缓存命中 | 相同问题（同一会话上下文）秒回时，消息底部显示「⚡ 缓存命中 · 原耗时 Xs」 |
| 示例问题 | 空状态页的快捷入口，点击即提问 |

**④ 多轮对话**：同一个会话里可以直接用指代追问，例如先问"销量最高的前 5 位艺术家是谁？"，再问"**那前 10 名呢？**"，Agent 会结合上文自动把 `LIMIT 5` 改为 `LIMIT 10`。想换个话题点"✨ 新会话"。

**⑤ 停止服务**：回到启动服务的终端按 `Ctrl + C`。

**常见问题**

| 现象 | 原因与处理 |
|---|---|
| **终端没有"启动 Web 服务"提示** | 大概率是运行了 `python main.py`——它是命令行入口（不带参数进 REPL，出现 `你:` 提示符即此情况），不提供网页服务。按 `exit` 退出，改用 `python server.py` |
| 页面打不开 / 空白 | 确认服务已启动；若改动过前端代码，需 `cd web/frontend && npm run build` 重新构建 |
| 端口被占用 | 换端口：`WEB_PORT=8080 python server.py`（或改 `.env` 的 `WEB_PORT`） |
| 提示"已有请求正在处理" | 同一时刻只处理一个问题（LLM 是瓶颈），等上一条回答完再发。若是关闭/刷新页面打断了旧请求，串行锁会自动释放并自愈，无需重启服务 |
| 返回"很抱歉，尝试多次仍失败" | 换一种更明确的问法，或换个话题重试；具体原因会显示在执行结果区 |
| **查询报 `Failed to fetch`** | 浏览器连不上后端。页面顶部会直接显示排查指引（含"重新检测"按钮），按顺序确认：① 终端的 `python server.py` 仍在运行且没报错退出；② 浏览器访问的是 `http://127.0.0.1:8000`（改过端口则与 `WEB_PORT` 一致）；③ 不要用 `file://` 双击打开 `index.html`——那样浏览器禁止请求接口。后端已放开 CORS，跨域访问（预览面板 / Vite dev 直连）不受限 |

### 3. 日常使用 · 命令行

```bash
conda activate py312

# 单次提问（适合脚本化/快速验证）
python main.py "销量最高的前5位艺术家是谁？"

# 交互模式：连续对话，支持多轮指代（注意：此模式不启动网页服务）
python main.py
#   你: 每种音乐流派各有多少首歌曲？
#   你: 那前5名呢？          ← 指代上文，自动理解为流派歌曲数 Top5
#   你: /new                ← 开新会话，清空对话记忆
#   你: exit                ← 退出
```

命令行会逐步打印节点流转（`get_schema → write_query → review_query → execute_query → generate_answer`）、每条 SQL、结果表格与总耗时。

### 4. 前端二次开发（可选，需 Node 18+）

```bash
cd web/frontend
npm install                  # 首次安装依赖（约 40MB，仅构建/开发需要）
npm run dev                  # 开发服务器 http://localhost:5173（热更新，/api 自动代理到 8000）
npm run build                # 构建产物到 dist/，由 server.py 托管（访问 http://127.0.0.1:8000 看效果）
```

> 开发模式（`npm run dev`）需要**同时**保持 `python server.py` 在 8000 端口运行；日常使用直接访问 8000 即可，不必启动 dev 服务器。
>
> **依赖目录不入库**：`web/frontend/node_modules` 是构建期缓存，仓库里不保留（已删）。只运行网页版不需要它；改前端代码时 `npm install` 一次即可，用完可随时 `rm -rf node_modules` 回收空间（`dist/` 构建产物 128KB 要保留，`server.py` 直接托管它）。

### 5. 运行测试

```bash
# 离线全量测试（97 项，mock LLM，无需真实 key）
pytest -v -m "not integration"

# 真实 LLM 集成测试（4 项，需配置 .env，含 Web SSE 端到端）
pytest -v -m integration

# 全部（101 项）
pytest -v
```

### 6. 性能基准

```bash
# CLI / Agent 侧：逐节点耗时 + LLM 调用次数
python tools/bench_perf.py                 # 内置 3 道题（易/中/难）
python tools/bench_perf.py "你的问题"       # 自定义

# Web 侧（需先启动 python server.py）：首 token / 各阶段耗时 / 事件数
python tools/bench_web.py                  # 严谨模式
python tools/bench_web.py --fast           # 极速模式
python tools/bench_web.py --repeat 2       # 多次采样（LLM 侧有波动，建议 ≥2 次）
python tools/bench_web.py --fast --cache  # 允许命中问答缓存（对比缓存收益）
```

> 基准脚本**默认禁用缓存**（请求带 `nocache`），测的是真实 LLM 延迟；只有加 `--cache` 才允许命中。

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
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `8000` | Web 服务监听地址与端口（`server.py`） |
| `WEB_WARMUP` | `true` | 启动预热（schema 缓存 + LLM 连接池），降低首问延迟 |
| `WEB_CACHE_TTL` | `600` | 问答缓存有效期（秒）；`0` 关闭。Chinook 为静态只读库，相同问题秒回 |
| `WEB_CACHE_SIZE` | `64` | 缓存条目上限（LRU） |
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

### Web 端优化（页面实测，`python tools/bench_web.py`）

| 指标 | 优化前 | 优化后（严谨） | 优化后（**极速**） | 提升 |
|---|---:|---:|---:|---|
| 首 token（TTFT，用户最直观的等待） | 3.66s | 3.42s | **1.85s** | **-49%** |
| 单问总耗时 | 4.45s | 4.56s | **2.83s** | **-36%** |
| 每次回答的 token 事件数 | 69 | 26 | **22** | **-62%** |
| 首事件（页面出现第一个进度） | 0.01s | 0.01s | 0.01s | — |

**优化手段（Web 侧）**

| 手段 | 说明 | 收益 |
|---|---|---|
| **极速模式（默认）** | 页面右上角可切换：`⚡ 极速` 跳过评审回路（write → execute → answer），`🛡 严谨` 保留评审把关。复杂查询建议切严谨 | 首 token -49% |
| **token 批量下发** | LLM 逐字流式会产生上百个事件，后端按 40ms 窗口合并下发（首块仍立即发），前端再按 `requestAnimationFrame` 每帧上屏一次 | 事件数 -62%，滚动不再抖 |
| **启动预热** | 启动时后台预建 schema 缓存与 LLM 连接池（`WEB_WARMUP`），把首次请求的前置开销挪到启动阶段 | 首问更稳 |
| **上下文瘦身** | "那前10名呢？"这类追问不再退化为全表上下文（改走销量/曲目核心表）；未命中关键词时全表兜底去掉样例行 | prefill 更短 |
| **等待反馈** | 等待期间显示实时计时（"正在查询… 2.3s"）与已完成的节点耗时；自动滚动仅在用户贴近底部时触发 | 感知延迟显著改善 |

### 本轮复测与新增优化（真实 GLM，3 题 × 2 次）

| 指标 | 严谨模式 | 极速模式（页面默认） | 极速 + 缓存命中 |
|---|---:|---:|---:|
| 首 token（TTFT） | 3.31s | **1.77s** | **0.00s** |
| 单问总耗时 | 4.11s | **2.56s** | **0.00~0.01s** |
| token 事件数 | 18 | 18 | 18（完整回放） |

> 与上一轮（严谨 3.48s / 极速 1.85s）基本持平，说明 LLM 往返延迟已接近下限；复杂题在严谨模式下仍需 6~7s，其中约 3.7s 是评审→改写→复审回路，属设计取舍，简单查询走默认极速即可。

本轮三项改动：

| 改动 | 说明 |
|---|---|
| **修复串行锁泄漏** | 客户端中途断开（关闭页面/刷新）时，同步 SSE 生成器的 `finally` 可能永不执行，导致 `_chat_lock` 永久占用、之后所有查询返回 409。改为幂等 `_LockGuard`（worker / 生成器 / background 三处释放）+ 超时自愈自动换锁；实测断开后下一次请求立即恢复 200 |
| **问答缓存** | Chinook 是静态只读库，缓存键 = 问题 + 模式 + 会话历史，命中则直接回放完整 SSE 事件（不占锁、不调 LLM），页面显示「⚡ 缓存命中 · 原耗时 Xs」。多轮上下文不同不会串答案；失败/空回答不缓存。可用 `WEB_CACHE_TTL=0` 关闭，基准脚本默认带 `nocache` |
| **前端答案重复修复** | `done` 事件到达时若 rAF 缓冲尚未上屏，兜底的完整 answer 会与缓冲 token 叠加导致回答出现两遍；改为先 flush 再兜底。等待计时刷新由 100ms 降为 200ms，减少无谓重渲染 |

## 日志

运行时会同时输出到**控制台**和 `logs/sql_agent.log`（滚动文件，1MB/份，保留 3 份），记录每个节点的流转：

- `INFO`：表命中、SQL 生成、执行成败、重试路由、回答完成、Web 请求入口与耗时
- `DEBUG`：结果预览、图构建细节（`.env` 中设 `LOG_LEVEL=DEBUG` 开启）
- `WARNING`：SQL 安全校验拦截、执行错误、重试耗尽、Web 并发拒绝

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
| 日志 | `logging` + RotatingFileHandler（1MB×3 滚动），`sql_agent.*` 命名空间（Web 侧为 `sql_agent.web`） |
| Web | FastAPI + SSE（`server.py`，`create_app(agent=...)` 依赖注入）；前端 Vue 3 + Vite + marked（SSE 用 fetch 流式解析） |
| 测试 | pytest + mock LLM（FakeLLM 脚本化响应），integration / query_review / optimize_always 标记隔离 |

## 开发规范

参与本项目的开发约定（节点职责、状态契约、安全红线、日志与测试规范）详见 [AGENTS.md](AGENTS.md)。
