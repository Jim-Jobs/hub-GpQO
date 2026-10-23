# SQL Agent

基于 chinook.db 的完整 SQL Agent 工作流 —— 让用户用自然语言提问，系统自动完成从**理解问题**到**给出答案**的全过程。

## 功能特性

- **数据库解析**：自动扫描所有表结构、字段类型、主键外键关系、字段统计（去重数、空值、最大/最小值、样例值）
- **Table 选择**：根据用户问题从全部表中筛选相关表，减少无关信息干扰，提升生成准确率
- **SQL 生成**：结合问题与表信息，调用大模型生成 SQL
- **SQL 校验**：校验语法正确性、表名有效性，并拦截非 SELECT 的危险语句
- **SQL 改写**：SQL 校验/执行失败时，将错误信息反馈给模型自动修复（最多重试多次）
- **SQL 执行**：执行 SELECT 查询，带 LIMIT 安全保护，防止返回超量数据
- **结果汇总**：将查询结果交由大模型生成自然语言回答（不区分结果行数，统一走 LLM）
- **Mock 模式**：内置模拟 LLM，无需 API Key 即可完整演示工作流

## 工作流架构

```
用户自然语言提问
    │
    ├─ ① 数据库解析 (DBParser)        → 表结构 / 字段统计 / 外键关系
    ├─ ② Table 选择 (TableSelector)   → 筛选相关表
    ├─ ③ SQL 生成 (SQLGenerator)      → 生成 SQL
    ├─ ④ SQL 校验 (SQLValidator)      → 语法 / 表名 / 语句类型校验
    │      └─ 失败 → ⑤ SQL 改写 (SQLRewriter) → 自动修复
    ├─ ⑥ SQL 执行                     → 带 LIMIT 保护地执行
    │      └─ 失败 → 触发 SQL 改写修复后重试
    └─ ⑦ 结果汇总 (ResultSummarizer)  → 自然语言回答
```

## 文件结构

| 文件 | 说明 |
|------|------|
| `sql_agent.py` | 核心代码，包含 LLM 适配器与全部 7 个工作流模块 |
| `demo.py` | 交互式演示脚本（含 Mock LLM 实现） |
| `run_demo.py` | Mock 模式非交互式运行脚本，一次性演示全部功能 |
| `run_real.py` | 真实大模型运行脚本（从环境变量读取 API Key） |
| `chinook.db` | SQLite 示例数据库（音乐商店） |
| `sql_agent_report.md` | 工作流执行报告 |
| `README.md` | 本说明文件 |

## 环境要求

- Python 3.10+
- 依赖：`sqlalchemy`、`pandas`、`pyjwt`、`requests`

```powershell
pip install sqlalchemy pandas pyjwt requests
```

## 快速开始

### 方式一：Mock 模式（无需 API Key）

直接运行非交互式演示，一次性查看数据库解析、SQL 校验、SQL 执行与完整工作流：

```powershell
python run_demo.py
```

或运行交互式演示：

```powershell
python demo.py
```

### 方式二：真实大模型模式

设置 API Key 后运行，即可使用真实大模型替代 Mock：

```powershell
$env:GLM_API_KEY="your_api_key_here"
python run_real.py     # 一键跑通完整工作流（推荐）
# 或 python demo.py    # 交互式演示
```

支持多家大模型服务商（通过 `llm_provider` 切换，或按 `llm_base_url` 自动识别）：

| provider | 默认模型 | 认证方式 |
|----------|----------|----------|
| `glm`（智谱） | glm-3-turbo | JWT（`id.secret` 格式 key） |
| `deepseek` | deepseek-chat | Bearer |
| `dashscope`（通义） | qwen-plus | Bearer |
| `openai` | gpt-4o-mini | Bearer |

> Agent 初始化时会自动校验 API Key，若无效会立即给出明确提示，不会在查询过程中才失败。

### 方式三：代码调用

```python
from sql_agent import SQLAgent

# GLM 示例
agent = SQLAgent(
    db_url="sqlite:///chinook.db",
    llm_api_key="your_api_key",
    llm_provider="glm",
)

# DeepSeek 示例（OpenAI 兼容）
agent = SQLAgent(
    db_url="sqlite:///chinook.db",
    llm_api_key="sk-xxxx",
    llm_provider="deepseek",
    llm_model="deepseek-chat",
)

result = agent.query("销量最高的音乐类型是什么？")
print(result.answer)
agent.print_steps(result)
```

## 核心类说明

| 类 | 职责 |
|----|------|
| `LLMClient` | 大模型客户端，支持 GLM（JWT 认证）与 OpenAI 兼容 API（Bearer 认证），并提供 `check_connection()` 连通性校验 |
| `DBParser` | 数据库解析器，扫描表结构、统计字段、提取外键，并提供 SQL 校验/执行能力 |
| `TableSelector` | 表选择器，根据问题选择相关表 |
| `SQLGenerator` | SQL 生成器 |
| `SQLValidator` | SQL 校验器 |
| `SQLRewriter` | SQL 改写器，负责错误修复 |
| `ResultSummarizer` | 结果汇总器，将数据转为自然语言 |
| `SQLAgent` | 主编排器，串联完整工作流并产出 `AgentResult` |

## 运行结果示例

以真实大模型（GLM）运行 `run_real.py`，端到端问答结果（最终回答由 LLM 生成自然语言）：

| 问题 | 最终回答 |
|------|----------|
| 总共有多少首歌曲？ | 总共有3503首歌曲。 |
| 销量最高的音乐类型？ | 销量最高的音乐类型是摇滚（Rock），共售出了835次。 |
| 哪个国家的客户最多？ | 美国的客户最多，共有13位。 |

Mock 模式下结果一致（3503 / Rock·835 / USA·13）。完整数据详见 [sql_agent_report.md](sql_agent_report.md)。

**验证记录**：2026-10-20 使用 GLM（`glm-3-turbo`）实测 `run_real.py`，API Key 校验通过，3/3 问题全部成功，SQL 校验与执行环节均无错误；最终回答由 LLM 生成自然语言（措辞可能随调用略有差异，数值稳定）。

## 输出结构

`SQLAgent.query()` 返回 `AgentResult` 对象：

| 字段 | 说明 |
|------|------|
| `question` | 用户原始问题 |
| `answer` | 最终自然语言回答 |
| `sql` | 实际执行的 SQL |
| `selected_tables` | 选中的相关表 |
| `result_data` | 原始查询结果（rows / columns / info） |
| `steps` | 各环节执行日志（步骤名 / 状态 / 耗时） |
| `success` | 整体是否成功 |

## 注意事项

- 执行器仅允许 `SELECT` 查询，会拦截 `INSERT`/`UPDATE`/`DELETE`/`DROP` 等语句
- 未显式指定 `LIMIT` 的查询会自动追加行数限制，避免返回超量数据
- Mock 模式仅用于演示与流程验证，真实问答效果需接入真实大模型
- API Key 请通过环境变量传入（如 `$env:GLM_API_KEY="..."`），**不要硬编码到代码或提交到版本库**，避免泄露
