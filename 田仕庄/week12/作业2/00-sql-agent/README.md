# Chinook SQL Agent

用自然语言查询 Chinook 音乐商店数据库。核心主张：**流程由 Python 代码编排，模型只在四个单点上做结构化输出**——不做自主 tool 调用、不做 agent 间 handoff，因此每条分支、每次重试都可预测、可测试、可回放。

- 模型接入：DeepSeek `deepseek-flash`（OpenAI 兼容端点），经 `langchain-openai` 的 `ChatOpenAI`
- 数据库：SQLite（`ref/chinook.db`，11 张业务表 / 3503 条曲目），**全程只读**
- 当前状态：`37 passed`，`ruff` 全绿；已从 openai-agents 迁移至 LangChain 并完成等价性验证

## 流水线

```
问题 → 选表(LLM) → 装配 schema 上下文 → 生成 SQL(LLM) → 校验(EXPLAIN,不执行)
                                                      ↓ 失败
                                             改写修复(LLM) ←─┘  最多 3 次
                                                      ↓ 通过
                                              执行(只读) → 汇总(LLM) → JSON
```

确定性的部分（解析库表、拼 schema、校验 SQL、执行、截断、总结 JSON）全是我们自己的代码；只有选表、生成、修复、汇总四个环节调模型。结果为空时直接返回"未查询到符合条件的数据。"，不调汇总模型；问题与库表无关时短路为澄清问题。

## 快速开始

```bash
# 1. 解释器：Python >= 3.10（本项目在 3.12.13 上验证）
python -V

# 2. 安装（必须带 --no-deps：环境里依赖已满足，重新解析只会带来版本漂移）
pip install -e . --no-deps

# 3. 配置密钥
cp .env.example .env        # 填入 OPENAI_API_KEY=sk-...

# 4. 提问
python -m sql_agent "总共有多少张专辑？"
```

也可以从标准输入读问题（`echo "有多少客户？" | python -m sql_agent`），或直接运行安装时生成的 `sql-agent` 入口脚本。

## 输出

日志走 stderr，最终 JSON 走 stdout；`error` 非空时进程退出码为 1。

```json
{
  "question": "总共有多少张专辑？",
  "answer": "数据库中共有 347 张专辑。",
  "sql": "SELECT COUNT(*) AS AlbumCount FROM albums",
  "tables": ["albums"],
  "row_count": 1,
  "truncated": false,
  "repair_attempts": 0,
  "evidence": [
    "SELECT COUNT(*) AS AlbumCount FROM albums 返回 AlbumCount = 347",
    "返回行数 1 行，未截断"
  ],
  "caveats": [],
  "error": null
}
```

`repair_attempts` 记录触发了几轮修复；`truncated` 表示结果超过 200 行被截断（此时 `row_count` 仍是**真实总行数**，由一次 `COUNT` 子查询补查获得，`rows` 才是被截断后的行）；`caveats` 是模型给出的口径提醒。结构由代码固定，但 `answer` / `evidence` / `caveats` 的措辞每次运行可能不同。

## 安全边界

- **只读连接**：SQLAlchemy 用 `sqlite:///file:<url-encoded-path>?mode=ro&uri=true`，裸 sqlite3 用 `Path.as_uri()?mode=ro`，不提供可写连接。
- **只校验不执行**：`SQLValidator` 仅用 `EXPLAIN` 编译，永不真正执行生成的语句；编译前先做单语句、`SELECT`/`WITH` 前缀、禁用关键字（跳过字符串与注释）三道检查。`REPLACE(x, a, b)` 是合法字符串函数、不在禁用列表；`REPLACE INTO` 等写语句由前缀检查拦截。
- **错误码可追溯**：`unsafe` / `syntax` / `unknown_object` / `ambiguous` / `runtime` / `timeout` / `empty`，既进日志也进修复 prompt。

## 配置

全部可通过环境变量或 `.env` 覆盖，默认值已够用。

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | 必填；回退读 `DEEPSEEK_API_KEY` |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | 换中转/网关时改这里 |
| `SQL_AGENT_MODEL` | `deepseek-flash` | 选表/生成/修复用的模型 |
| `SQL_AGENT_SUMMARY_MODEL` | `deepseek-flash` | 汇总模型 |
| `SQL_AGENT_THINKING` | `auto` | `auto`/`enabled`/`disabled`，见下方「三个坑」 |
| `SQL_AGENT_DB` | `ref/chinook.db` | 相对路径按项目根解析，与 cwd 无关 |
| `SQL_AGENT_MAX_REPAIR_ATTEMPTS` | `3` | 修复预算 |
| `SQL_AGENT_MAX_ROWS` | `200` | 结果截断阈值 |
| `SQL_AGENT_QUERY_TIMEOUT` | `5.0` | 单次查询超时（秒） |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | `false` / — | 开启后每次运行上报一条 trace（含完整 prompt 与最多 50 行结果，注意隐私） |

## 项目结构

```
src/sql_agent/
  llm.py            # ChatOpenAI 工厂（lru_cache）+ structured() 结构化输出适配器
  chains/           # 四条 LLM 链：table_selector / sql_generator / sql_rewriter / result_summarizer
  workflow.py       # 编排主体：状态机、重试预算、短路判断
  db/               # catalog（库表画像）/ validator（EXPLAIN 校验）/ executor（只读执行）
  models.py         # Pydantic 契约：TableSelection / SQLDraft / FinalAnswer / WorkflowOutput
  config.py         # 配置与可选 .env 加载
tests/              # 37 个用例，全部离线（无 key、不联网）
ref/                # 只读素材：chinook.db 与原始参考实现，不作为运行时依赖
docs/               # 设计文档与迁移方案
```

## 测试

```bash
python -m pytest -q                 # 37 passed，无需 API key
python -m ruff check src tests      # All checks passed
```

四条链的工厂函数在测试中被 monkeypatch，因此默认测试完全不联网。测试用 `PROJECT_ROOT` 而非 cwd 定位数据库，任意目录执行结果一致；从仓库外运行需要先完成 `pip install -e .`。

## 三个容易踩的坑

1. **思考模式默认开启**。`deepseek-flash` 的 thinking 默认 on 且 `reasoning_effort=high`；此模式下 `temperature` 设置不报错但**完全不生效**，不要指望 `temperature=0` 带来确定性。要压延迟用 `SQL_AGENT_THINKING=disabled`。
2. **结构化输出不能用 `with_structured_output(method="function_calling")`**。它会把 `tool_choice` 钉成具体函数名，思考模式直接 `400 Thinking mode does not support this tool_choice`。本项目用 `bind_tools(tool_choice="auto")` + `PydanticToolsParser` 绕开。
3. **模型名是 `deepseek-flash`**。`deepseek-v4-flash` 是已下线模型的旧别名，`deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 弃用。

## 文档

| 文件 | 内容 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 操作手册：环境命令、库表事实、实现约束 |
| [`docs/agent-design.md`](docs/agent-design.md) | 架构设计：数据契约、状态机、各环节职责与权限边界 |
| [`docs/langchain-migration-plan.md`](docs/langchain-migration-plan.md) | 从 openai-agents 迁移到 LangChain 的完整方案与实施记录（v2.1） |
| [`.env.example`](.env.example) | 所有配置项的占位与说明 |

## 已知局限

- SQL 只支持 SQLite 方言；多语句、写操作、DDL 一律拒绝。
- 汇总模型可能给出结果集之外的表述，`evidence`/`caveats` 只作参考，数字以执行结果为准。
- LangSmith 链路追踪代码已就绪，但本机未配置 key，trace 树尚未做真机验证。
