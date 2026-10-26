# ChatBI / Data Agent 技术设计文档

> 版本：v1.0
> 日期：2026-10-22
> 定位：回答「怎么实现」，含架构、技术选型、核心流水线设计、安全、部署、评测。对应可运行实现见 `04_SQL-Code-Agent-Demo/sql_agent_workflow.py`。

---

## 1. 总体架构

```
┌──────────────┐    HTTP/SSE     ┌──────────────────────────────────┐
│  前端 (Web)   │ ◄──────────────► │       后端服务（FastAPI）          │
│ React/Streamlit│                │  会话管理 · 鉴权 · 审计 · 流式响应   │
└──────────────┘                 └───────────────┬──────────────────┘
                                                  │ 调用
                                    ┌─────────────▼──────────────────┐
                                    │      ChatBI Agent 编排层         │
                                    │  (SQLAgent：七步流水线)          │
                                    └──────┬─────────────────────────┘
                              ┌────────────┴────────────┐
                     ┌────────▼────────┐      ┌─────────▼─────────┐
                     │  LLM 服务层       │      │  数据访问层        │
                     │ 通义/DS/豆包/GPT │      │  DBParser(元数据)  │
                     │ (OpenAI 兼容协议) │      │  只读连接 · 语义层  │
                     └─────────────────┘      └───────────────────┘
```

**设计原则**：
- **分层解耦**：LLM（大脑）与数据库（数据）分离，通过 Agent 编排层协调。
- **每一步可替换**：选表/生成/校验/改写/汇总各自独立，便于单独调优或替换模型。
- **安全优先**：数据访问层只提供只读连接。

---

## 2. 技术选型

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 后端框架 | FastAPI | 高性能、原生支持异步 + SSE 流式 |
| 前端 | Streamlit（MVP）/ React + Ant Design（正式） | 快速验证 vs 正式产品 |
| LLM 访问 | OpenAI SDK（兼容协议） | 一套代码接入通义/DeepSeek/豆包/GPT |
| Agent 框架 | OpenAI Agents SDK / LangGraph（可选） | 复杂多步编排；本 MVP 用显式流水线即可 |
| 数据库访问 | SQLAlchemy（ORM）+ Inspect | 跨 MySQL/SQLite/PG，自动解析元数据 |
| 数据库 | MySQL / PostgreSQL（生产）/ SQLite（演示） | 关系型数据库 |
| 向量检索 | 向量库（可选，二期 RAG） | 相似问答检索、few-shot 缓存 |
| 可观测 | 结构化日志 + 埋点 | 追踪每次问答全链路 |

---

## 3. SQL Agent 流水线详细设计（核心）

对应七步流程，是 ChatBI 的心脏。以下是每一步的输入、处理、输出与关键实现。

```
question ─► ① 数据库解析 ─► ② 表选择 ─► ③ SQL 生成 ─► ④ 校验 ─(失败)─► ⑤ 改写 ─┐
                                                                         │ 通过
            ⑦ 结果汇总 ◄─ ⑥ SQL 执行 ◄─────────────────────────────────────┘
```

### ① 数据库解析（DBParser）

- **职责**：把数据库结构「翻译」成 LLM 可读的上下文。
- **产出**：
  - 表清单（`table_names`）
  - 表结构（字段名、类型、是否主键）
  - 外键关系（`foreign_keys`）
  - 字段统计（distinct 个数、min/max、众数、缺失数、随机样例）
  - 表样例（首行数据）
- **实现要点**：SQLAlchemy `inspect` 读元数据；用聚合查询统计字段；输出 `get_schema_summary()` / `get_relations_str()` / `get_table_sample()`。

### ② 表选择（Table Selection）

- **输入**：用户问题 + 全量表结构摘要 + 外键关系。
- **输出**：相关表名列表（如 `["artists", "albums"]`）。
- **实现**：LLM 按 prompt 返回 JSON 数组；代码稳健解析 JSON（`_extract_json_array`），失败时回退到关键词匹配。
- **目的**：缩小 SQL 生成时的上下文范围，减少幻觉、降低成本。

### ③ SQL 生成（SQL Generation）

- **输入**：问题 + 所选表的结构 + 外键 + 样例。
- **输出**：一条 SQLite/MySQL 可执行 SQL。
- **实现**：prompt 要求「只输出 SQL」，代码剥离 markdown 代码块（`_extract_sql`）。

### ④ SQL 校验（Validation）

- **职责**：执行前验证 SQL 是否可跑。
- **实现**：`parser.check_sql(sql)` 在事务中 `execute` 试探，捕获异常返回 `(bool, err_msg)`。
- **注意**：生产环境必须用**只读账号**，且校验时避免副作用（只允许 SELECT / WITH / EXPLAIN）。

### ⑤ SQL 改写（Rewriting）

- **触发**：校验失败。
- **输入**：问题 + 表结构 + 外键 + 原 SQL + **报错信息**。
- **输出**：修正后的 SQL。
- **实现**：把报错信息回喂给 LLM 修正；循环最多 `max_retries` 次（默认 3）。
- **关键点**：报错信息是改写的核心上下文（对应课程 Q39「重写 SQL 要带表结构信息」）。

### ⑥ SQL 执行（Execution）

- **实现**：只读事务执行，返回带列名的 `DataFrame`（`execute_sql_df`）。
- **安全**：拒绝非查询语句。

### ⑦ 结果汇总（Summarization）

- **输入**：问题 + SQL + 结果表格（markdown）。
- **输出**：自然语言回答（如「albums 表一共有 347 张专辑」）。
- **实现**：LLM 将结构化结果转自然语言，`temperature` 适当调低保证稳定。

### 流水线伪代码

```python
class SQLAgent:
    def run(self, question):
        tables   = self.select_tables(question)            # ②
        sql      = self.generate_sql(question, tables)     # ③
        for i in range(self.max_retries):                  # ④⑤ 循环
            ok, err = self.check_sql(sql)
            if ok: break
            sql = self.rewrite_sql(question, tables, sql, err)
        result = self.execute_sql(sql)                     # ⑥
        answer = self.summarize(question, sql, result)     # ⑦
        return answer
```

> 完整可运行实现见 `sql_agent_workflow.py`，其中 `OpenAILLM` 为真实模型实现、`OfflineLLM` 为无 key 演示实现。

---

## 4. LLM 编排设计

### 4.1 多模型适配

采用 **OpenAI 兼容协议**统一接入，切换模型只需改三个配置：

```python
OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 通义
# DeepSeek:  https://api.deepseek.com/v1
# 豆包:      https://ark.cn-beijing.volces.com/api/v3
# GPT:       https://api.openai.com/v1
LLM_MODEL = "qwen-max"   # 或 deepseek-chat / doubao-pro / gpt-4o
```

### 4.2 Prompt 策略

- **结构化输出**：选表要求输出 JSON 数组；SQL 要求只输出 SQL；代码侧做稳健解析兜底。
- **低温生成**：SQL 生成用 `temperature=0`，汇总可适当放宽。
- **注入真实元数据**：所有 prompt 都带上真实 schema + 外键 + 样例，对抗幻觉。
- **报错回喂**：改写 prompt 注入执行报错，形成自我修正闭环。

### 4.3 成本与缓存（二期）

- 相似问题检索（向量相似度）→ 命中缓存 SQL，省 token。
- 对高频问题做 few-shot 缓存。

---

## 5. 数据访问与安全设计

### 5.1 只读访问

- 数据库使用**只读账号**（`SELECT` 权限）。
- 执行前白名单校验：仅允许 `SELECT` / `WITH` 开头；显式拒绝 `INSERT/UPDATE/DELETE/DROP/ALTER`。
- 生产环境在独立从库/数仓执行，避免影响主库。

### 5.2 字段级权限与脱敏

- 元数据层标记敏感字段（工资、手机号、身份证、邮箱等）。
- 无权限用户查询敏感字段 → 拦截返回「无权限」。
- 有权限用户 → 结果脱敏（如手机号 `138****1234`）。

### 5.3 审计

- 记录：用户、时间、原始问题、生成的 SQL、执行结果行数、是否命中权限拦截。

---

## 6. API 设计（后端接口）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | 发起问答，返回流式结果（SSE） |
| GET | `/api/session/{id}/history` | 获取会话历史 |
| POST | `/api/feedback` | 提交点赞/点踩反馈 |
| GET | `/api/datasource` | 数据源/元数据列表 |
| POST | `/api/datasource` | 新增数据源（触发解析） |
| GET | `/api/metrics` | 指标/维度字典 |

**流式响应示例（SSE）**：

```
data: {"type":"thinking","content":"选择表 artists, albums"}
data: {"type":"sql","content":"SELECT ..."}
data: {"type":"data","content":{...}}
data: {"type":"answer","content":"albums 表一共有 347 张专辑。"}
data: [DONE]
```

---

## 7. 评测方案

| 层 | 评测内容 |
| --- | --- |
| SQL 层 | 执行成功率、语法正确率 |
| 结果层 | 生成结果与人工标注理想结果一致性（执行准确率） |
| 端到端 | 回答可用率（人工评分）、多轮上下文正确率 |
| 安全 | 越权/注入拦截率 |

**评测集构建**：由「问题 + 理想 SQL + 理想结果」三元组构成，结合线上真实问题 + 用户反馈沉淀。

---

## 8. 部署与运维

- **服务化**：FastAPI 服务容器化部署；LLM 与数据库均走网络访问。
- **异步**：对外接口统一异步（课程 Q5：对外服务推荐异步）。
- **日志**：结构化日志，记录全链路 trace，便于排查幻觉与性能问题。
- **灰度**：新模型/新 prompt 先在评测集回归，再灰度上线。

---

## 9. 扩展方向

- **MCP 工具化**：把「数据库查询」封装成 MCP Tool，供更通用的 Agent 调用。
- **沙箱执行**：复杂多步取数用代码 Agent + 沙箱隔离执行（课程 Q15/沙箱 Agent）。
- **RAG**：检索历史相似问答、指标口径，提升准确率（课程 Q62）。
- **主动分析**：定时任务 + 异常检测，主动推送数据洞察。
